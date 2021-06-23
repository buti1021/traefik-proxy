"""Traefik implementation

Custom proxy implementations can subclass :class:`Proxy`
and register in JupyterHub config:

.. sourcecode:: python

    from mymodule import MyProxy
    c.JupyterHub.proxy_class = MyProxy

Route Specification:

- A routespec is a URL prefix ([host]/path/), e.g.
  'host.tld/path/' for host-based routing or '/path/' for default routing.
- Route paths should be normalized to always start and end with '/'
"""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

from concurrent.futures import ThreadPoolExecutor
import escapism
from urllib.parse import urlparse

from tornado.concurrent import run_on_executor
from traitlets import Any, default, Unicode

from jupyterhub.utils import maybe_future
from jupyterhub_traefik_proxy import TKvProxy


class TraefikEtcdProxy(TKvProxy):
    """JupyterHub Proxy implementation using traefik and etcd"""

    executor = Any()

    @default("provider_name")
    def _provider_name(self):
        return "etcd"

    etcd_client_ca_cert = Unicode(
        config=True,
        allow_none=True,
        default_value=None,
        help="""Etcd client root certificates""",
    )

    etcd_client_cert_crt = Unicode(
        config=True,
        allow_none=True,
        default_value=None,
        help="""Etcd client certificate chain
            (etcd_client_cert_key must also be specified)""",
    )

    etcd_client_cert_key = Unicode(
        config=True,
        allow_none=True,
        default_value=None,
        help="""Etcd client private key
            (etcd_client_cert_crt must also be specified)""",
    )

    @default("executor")
    def _default_executor(self):
        return ThreadPoolExecutor(1)

    @default("kv_url")
    def _default_kv_url(self):
        return "http://127.0.0.1:2379"

    @default("kv_client")
    def _default_client(self):
        etcd_service = urlparse(self.kv_url)
        try:
            import etcd3
        except ImportError:
            raise ImportError("Please install etcd3 package to use traefik-proxy with etcd3")
        kwargs = {
            'host': etcd_service.hostname,
            'port': etcd_service.port,
            'ca_cert': self.etcd_client_ca_cert,
            'cert_cert': self.etcd_client_cert_crt,
            'cert_key': self.etcd_client_cert_key,
        }
        if self.kv_password:
            kwargs.update({
                'user': self.kv_username,
                'password': self.kv_password
            })
        return etcd3.client(**kwargs)

    def _clean_resources(self):
        super()._clean_resources()
        self.kv_client.close()

    @run_on_executor
    def _etcd_transaction(self, success_actions):
        status, response = self.kv_client.transaction(
            compare=[], success=success_actions, failure=[]
        )
        return status, response

    @run_on_executor
    def _etcd_get(self, key):
        value, _ = self.kv_client.get(key)
        return value

    @run_on_executor
    def _etcd_get_prefix(self, prefix):
        routes = self.kv_client.get_prefix(prefix)
        return routes

    def _define_kv_specific_static_config(self):
        self.log.debug("Setting up the etcd provider in the static config")
        url = urlparse(self.kv_url)
        self.static_config.update({"providers" : {
            "etcd" : {
                "endpoints": [url.netloc],
                "rootKey": self.kv_traefik_prefix,
            }
        } })
        if self.kv_username and self.kv_password:
            self.static_config["providers"]["etcd"].update({
                "username": self.kv_username,
                "password": self.kv_password
            })

    async def _kv_atomic_add_route_parts(
        self, jupyterhub_routespec, target, data, route_keys, rule
    ):
        jupyterhub_target = self.kv_separator.join(
            [self.kv_jupyterhub_prefix, "targets", escapism.escape(target)]
        )
        success = [
            self.kv_client.transactions.put(jupyterhub_routespec, target),
            self.kv_client.transactions.put(jupyterhub_target, data),
            self.kv_client.transactions.put(route_keys.service_url_path, target),
            self.kv_client.transactions.put(
                route_keys.router_service_path, route_keys.service_alias
            ),
            self.kv_client.transactions.put(route_keys.router_rule_path, rule),
        ]
        status, response = await maybe_future(self._etcd_transaction(success))
        return status, response

    async def _kv_atomic_delete_route_parts(self, jupyterhub_routespec, route_keys):
        value = await maybe_future(self._etcd_get(jupyterhub_routespec))
        if value is None:
            self.log.warning(
                f"Route {jupyterhub_routespec} doesn't exist. Nothing to delete"
            )
            return True, None

        jupyterhub_target = self.kv_separator.join(
            [self.kv_jupyterhub_prefix, "targets", escapism.escape(value.decode())]
        )

        success = [
            self.kv_client.transactions.delete(jupyterhub_routespec),
            self.kv_client.transactions.delete(jupyterhub_target),
            self.kv_client.transactions.delete(route_keys.service_url_path),
            self.kv_client.transactions.delete(route_keys.router_service_path),
            self.kv_client.transactions.delete(route_keys.router_rule_path),
        ]
        status, response = await maybe_future(self._etcd_transaction(success))
        return status, response

    async def _kv_get_target(self, jupyterhub_routespec):
        value = await maybe_future(self._etcd_get(jupyterhub_routespec))
        if value is None:
            return None
        return value.decode()

    async def _kv_get_data(self, target):
        value = await maybe_future(self._etcd_get(target))
        if value is None:
            return None
        return value

    async def _kv_get_route_parts(self, kv_entry):
        key = kv_entry[1].key.decode()
        value = kv_entry[0].decode()

        # Strip the "/jupyterhub/routes/" prefix from the routespec and unescape it
        sep = self.kv_separator
        route_prefix = sep.join([self.kv_jupyterhub_prefix, "routes" + sep])
        target_prefix = sep.join([self.kv_jupyterhub_prefix, "targets"])
        routespec = escapism.unescape(key.replace(route_prefix, "", 1))
        etcd_target = sep.join([target_prefix, escapism.escape(value)])
        target = escapism.unescape(etcd_target.replace(target_prefix + sep, "", 1))
        data = await self._kv_get_data(etcd_target)

        return routespec, target, data

    async def _kv_get_jupyterhub_prefixed_entries(self):
        sep = self.kv_separator
        routespecs_prefix = sep.join([self.kv_jupyterhub_prefix, "routes" + sep])
        routes = await maybe_future(self._etcd_get_prefix(routespecs_prefix))
        return routes

    async def persist_dynamic_config(self):
        data = self.flatten_dict_for_kv(self.dynamic_config, prefix=self.kv_traefik_prefix)
        transactions = []
        for k, v in data.items():
            transactions.append(self.kv_client.transactions.put(k, v))
        status, response = await maybe_future(self._etcd_transaction(transactions))
        return status, response

