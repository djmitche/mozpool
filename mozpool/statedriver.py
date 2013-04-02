# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import abc
import sys
import time
import threading
import traceback
import logging
import config

####
# Driver

POLL_FREQUENCY = 10

class StateDriver(threading.Thread):
    """
    A generic state-machine driver.  This handles timeouts, as well as handling
    any events triggered externally.
    """
    __metaclass__ = abc.ABCMeta

    # override all these
    state_machine_cls = None
    logger_name = 'state'
    thread_name = 'StateDriver'
    log_db_handler = None

    def __init__(self, db, poll_frequency=POLL_FREQUENCY):
        threading.Thread.__init__(self, name=self.thread_name)
        self.setDaemon(True)
        self._stop = False
        self.db = db
        self.poll_frequency = poll_frequency
        self.logger = logging.getLogger(self.logger_name)
        self.log_handler = self.log_db_handler(db)
        self.logger.addHandler(self.log_handler)

    def stop(self):
        self._stop = True
        if self.isAlive():
            self.join()
        self.logger.removeHandler(self.log_handler)

    def run(self):
        try:
            # a tight loop that just launches the polling in a thread.  Then, if
            # it takes more than the poll interval, we can log loudly, but there's
            # nothing in this loop that's at risk of breaking
            while True:
                if self._stop:
                    self.logger.info("stopping on request")
                    break

                started_at = time.time()
                polling_thd = threading.Thread(target=self._tick)
                polling_thd.setDaemon(1)
                polling_thd.start()

                time.sleep(self.poll_frequency)
                # if the thread is still alive, we have a problem
                delay = 1
                while polling_thd.isAlive():
                    elapsed = time.time() - started_at
                    self.logger.warning("polling thread still running at %ds; not starting another" % elapsed)
                    self.write_current_frames()
                    time.sleep(delay)
                    # exponential backoff up to 1m
                    delay = delay if delay > 60 else delay * 1.1
        except Exception:
            self.logger.error("run loop failed", exc_info=True)
        finally:
            self.logger.warning("run loop returned (this should not happen!)")

    def write_current_frames(self):
        filename = "/tmp/current-frames-%d" % time.time()
        with open(filename, "w") as f:
            for thd, frame in sys._current_frames().items():
                f.write("%d\n%s\n\n" % (thd, "".join(traceback.format_stack(frame))))
        self.logger.warning(" wrote %r" % (filename,))

    def _tick(self):
        hb_file = config.get('server', 'heartbeat_file')
        if hb_file:
            open(hb_file, "w")

        try:
            self.poll_for_timeouts()
            self.poll_others()
        except Exception:
            self.logger.error("failure in _tick", exc_info=True)
            # don't worry, we'll get called again, for surez..

    def handle_event(self, machine_name, event, args):
        """
        Handle an event for a particular device, with optional arguments
        specific to the event.
        """
        machine = self._get_machine(machine_name)
        machine.handle_event(event, args)

    def handle_timeout(self, machine_name):
        """
        Handle a timeout for the given machine.
        """
        machine = self._get_machine(machine_name)
        try:
            machine.handle_timeout()
        except:
            self.logger.error("(ignored) error while handling timeout:",
                            exc_info=True)

    def conditional_state_change(self, machine_name, old_state, new_state):
        """
        Transition to NEW_STATE only if the device is in OLD_STATE.
        Returns True on success, False on failure.
        """
        machine = self._get_machine(machine_name)
        return machine.conditional_goto_state(old_state, new_state)

    def poll_for_timeouts(self):
        for machine_name in self._get_timed_out_machine_names():
            self.logger.info("handling timeout on %s" % machine_name)
            self.handle_timeout(machine_name)

    def _get_machine(self, machine_name):
        return self.state_machine_cls(machine_name, self.db)

    def poll_others(self):
        """
        Override with any activities to be performed each pass through the
        loop.
        """
        pass

    @abc.abstractmethod
    def _get_timed_out_machine_names(self):
        return []


####
# Logging handler

class DBHandler(logging.Handler):

    object_type = ''

    def __init__(self, db):
        super(DBHandler, self).__init__()
        self.db = db
        # pick the appropriate sub-object of db based on object_type
        self.db_methods = {
            'request' : db.requests,
            'device' : db.devices,
        }[self.object_type]

    def emit(self, record):
        logger = record.name.split('.')
        if len(logger) != 2 or logger[0] != self.object_type:
            return
        name = logger[1]

        msg = self.format(record)
        self.db_methods.log_message(name, msg, source='statemachine')
