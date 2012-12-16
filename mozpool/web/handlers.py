# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Functions common to all handlers."""

import time
import threading
import web.webapi
from mozpool import config
from mozpool.db import data

nocontent = NoContent = web.webapi._status_code("204 No Content")

def deviceredirect(function):
    """
    Generate a redirect when a request is made for a device that is not
    managed by this instance of the service.
    """
    def wrapped(self, id, *args):
        try:
            server = data.get_server_for_device(id)
        except data.NotFound:
            raise web.notfound()
        if server != config.get('server', 'fqdn'):
            raise web.found("http://%s%s" % (server, web.ctx.path))
        # otherwise, send an access-control header, so that pages in other domains can
        # call this API endpoint without trouble
        fqdns = data.all_imaging_servers()
        # this needs to be a sequence of headers, not one whitespace-joined header
        for fqdn in fqdns:
            origin = 'http://%s' % fqdn
            web.header('Access-Control-Allow-Origin', origin)
        return function(self, id, *args)
    return wrapped

class InMemCache:
    """
    Mixin for handler classes that want an in-memory cache for their data.
    This is a simple one-variable cache.

    Set CACHE_TTL as a class-level variable, and implement cache_fetch.
    This class provides cache_get.

    The class variable cache_expires can be used to get the expiration time for HTTP headers, etc.
    """

    CACHE_TTL = 60

    class __metaclass__(type):
        def __new__(meta, classname, bases, classDict):
            cls = type.__new__(meta, classname, bases, classDict)
            cls.cache_data = None
            cls.cache_expires = 0
            cls.cache_lock = threading.Lock()
            return cls

    def cache_fetch(self):
        print "FETCH"
        raise NotImplementedError

    def cache_get(self):
        cls = self.__class__
        with cls.cache_lock:
            if cls.cache_expires > time.time():
                return cls.cache_data
            cls.cache_data = self.cache_fetch()
            cls.cache_expires = time.time() + cls.CACHE_TTL 
            return cls.cache_data
