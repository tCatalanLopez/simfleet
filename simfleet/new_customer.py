import json
import time
from asyncio import CancelledError

from loguru import logger
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from spade.template import Template
from .geolocated_agent import GeoLocatedAgent

from .helpers import random_position
from .protocol import (
    REQUEST_PROTOCOL,
    TRAVEL_PROTOCOL,
    REQUEST_PERFORMATIVE,
    ACCEPT_PERFORMATIVE,
    REFUSE_PERFORMATIVE,
    QUERY_PROTOCOL,
)
from .utils import (
    CUSTOMER_WAITING,
    CUSTOMER_IN_DEST,
    TRANSPORT_MOVING_TO_CUSTOMER,
    CUSTOMER_IN_TRANSPORT,
    TRANSPORT_IN_CUSTOMER_PLACE,
    CUSTOMER_LOCATION,
    StrategyBehaviour,
    request_path,
    status_to_str,
)


class NewCustomerAgent(GeoLocatedAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        
        self.fleetmanagers = None
        self.status = CUSTOMER_WAITING
        self.transport_assigned = None
        self.waiting_for_pickup_time = None
        self.pickup_time = None

        # type_service y fleet type no entiendo muy bien en que se diferencian
        self.type_service = "taxi"

    def run_strategy(self):
        """import json
        Runs the strategy for the customer agent.
        """
        if not self.running_strategy:
            template1 = Template()
            template1.set_metadata("protocol", REQUEST_PROTOCOL)
            template2 = Template()
            template2.set_metadata("protocol", QUERY_PROTOCOL)
            self.add_behaviour(self.strategy(), template1 | template2)
            self.running_strategy = True
    
    async def setup(self):
        try:
            template = Template()
            template.set_metadata("protocol", TRAVEL_PROTOCOL)
            travel_behaviour = TravelBehaviour()
            self.add_behaviour(travel_behaviour, template)
            while not self.has_behaviour(travel_behaviour):
                logger.warning(
                    "Customer {} could not create TravelBehaviour. Retrying...".format(
                        self.agent_id
                    )
                )
                self.add_behaviour(travel_behaviour, template)
            self.ready = True
        except Exception as e:
            logger.error(
                "EXCEPTION creating TravelBehaviour in Customer {}: {}".format(
                    self.agent_id, e
                )
            )

    def set_fleetmanager(self, fleetmanagers):
        """
        Sets the fleetmanager JID address
        Args:
            fleetmanagers (str): the fleetmanager jid

        """
        self.fleetmanagers = fleetmanagers

    def set_target_position(self, coords=None):
        """
        Sets the target position of the customer (i.e. its destination).
        If no position is provided the destination is setted to a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        if coords:
            self.dest = coords
        else:
            self.dest = random_position()
        logger.debug(
            "Customer {} target position is {}".format(self.agent_id, self.dest)
        )

    def is_in_destination(self):
        """
        Checks if the customer has arrived to its destination.

        Returns:
            bool: whether the customer is at its destination or not
        """
        return self.status == CUSTOMER_IN_DEST or self.get_position() == self.dest

    def get_waiting_time(self):
        """
        Returns the time that the agent was waiting for a transport, from its creation until it gets into a transport.

        Returns:
            float: The time the customer was waiting.
        """
        if self.init_time:
            if self.pickup_time:
                t = self.pickup_time - self.init_time
            elif not self.stopped:
                t = time.time() - self.init_time
                self.waiting_for_pickup_time = t
            else:
                t = self.waiting_for_pickup_time
            return t
        return None

    def get_pickup_time(self):
        """
        Returns the time that the customer was waiting to be picked up since it has been assigned to a transport.

        Returns:
            float: The time that the customer was waiting to a transport since it has been assigned.
        """
        if self.pickup_time:
            return self.pickup_time - self.waiting_for_pickup_time
        return None

    def to_json(self):
        """
        Serializes the main information of a customer agent to a JSON format.
        It includes the id of the agent, its current position, the destination coordinates of the agent,
        the current status, the transport that it has assigned (if any) and its waiting time.

        Returns:
            dict: a JSON doc with the main information of the customer.

            Example::

                {
                    "id": "cphillips",
                    "position": [ 39.461327, -0.361839 ],
                    "dest": [ 39.460599, -0.335041 ],
                    "status": 24,
                    "transport": "ghiggins@127.0.0.1",
                    "waiting": 13.45
                }
        """
        t = self.get_waiting_time()
        data = super().to_json()
        data.update({
            "waiting": float("{0:.2f}".format(t)) if t else None,
            "transport": self.transport_assigned.split("@")[0]
            if self.transport_assigned
            else None
        })
        return data


class TravelBehaviour(CyclicBehaviour):
    """
    This is the internal behaviour that manages the movement of the customer.
    It is triggered when the transport informs the customer that it is going to the
    customer's position until the customer is dropped in its destination.
    """

    async def on_start(self):
        logger.debug("Customer {} started TravelBehavior.".format(self.agent.name))

    async def run(self):
        try:
            msg = await self.receive(timeout=5)
            if not msg:
                return
            content = json.loads(msg.body)
            logger.debug("Customer {} informed of: {}".format(self.agent.name, content))
            if "status" in content:
                status = content["status"]
                if status != CUSTOMER_LOCATION:
                    logger.debug(
                        "Customer {} informed of status: {}".format(
                            self.agent.name, status_to_str(status)
                        )
                    )
                if status == TRANSPORT_MOVING_TO_CUSTOMER:
                    logger.info(
                        "Customer {} waiting for transport.".format(self.agent.name)
                    )
                    self.agent.waiting_for_pickup_time = time.time()
                elif status == TRANSPORT_IN_CUSTOMER_PLACE:
                    self.agent.status = CUSTOMER_IN_TRANSPORT
                    logger.info("Customer {} in transport.".format(self.agent.name))
                    self.agent.pickup_time = time.time()
                elif status == CUSTOMER_IN_DEST:
                    self.agent.status = CUSTOMER_IN_DEST
                    self.agent.end_time = time.time()
                    logger.info(
                        "Customer {} arrived to destination after {} seconds.".format(
                            self.agent.name, self.agent.total_time()
                        )
                    )
                elif status == CUSTOMER_LOCATION:
                    coords = content["location"]
                    self.agent.set("current_pos", coords)
        except CancelledError:
            logger.debug("Cancelling async tasks...")
        except Exception as e:
            logger.error(
                "EXCEPTION in Travel Behaviour of Customer {}: {}".format(
                    self.agent.name, e
                )
            )


class CustomerStrategyBehaviour(StrategyBehaviour):
    """
    Class from which to inherit to create a transport strategy.
    You must overload the ``run`` coroutine

    Helper functions:
        * ``send_request``
        * ``accept_transport``
        * ``refuse_transport``
    """

    async def on_start(self):
        """
        Initializes the logger and timers. Call to parent method if overloaded.
        """
        logger.debug(
            "Strategy {} started in customer {}".format(
                type(self).__name__, self.agent.name
            )
        )
        self.agent.init_time = time.time()

    async def send_get_managers(self, content=None):
        """
        Sends an ``spade.message.Message`` to the DirectoryAgent to request a managers.
        It uses the QUERY_PROTOCOL and the REQUEST_PERFORMATIVE.
        If no content is set a default content with the type_service that needs
        Args:
            content (dict): Optional content dictionary
        """
        if content is None or len(content) == 0:
            content = self.agent.fleet_type
        msg = Message()
        msg.to = str(self.agent.directory_id)
        msg.set_metadata("protocol", QUERY_PROTOCOL)
        msg.set_metadata("performative", REQUEST_PERFORMATIVE)
        msg.body = content
        await self.send(msg)

        logger.info(
            "Customer {} asked for managers to directory {} for type {}.".format(
                self.agent.name, self.agent.directory_id, self.agent.type_service
            )
        )

    async def send_request(self, content=None):
        """
        Sends an ``spade.message.Message`` to the fleetmanager to request a transport.
        It uses the REQUEST_PROTOCOL and the REQUEST_PERFORMATIVE.
        If no content is set a default content with the customer_id,
        origin and target coordinates is used.

        Args:
            content (dict): Optional content dictionary
        """
        if not self.agent.dest:
            self.agent.dest = random_position()
        if content is None or len(content) == 0:
            content = {
                "customer_id": str(self.agent.jid),
                "origin": self.agent.get("current_pos"),
                "dest": self.agent.dest,
            }

        if self.agent.fleetmanagers is not None:
            for (
                fleetmanager
            ) in self.agent.fleetmanagers.keys():  # Send a message to all FleetManagers
                msg = Message()
                msg.to = str(fleetmanager)
                msg.set_metadata("protocol", REQUEST_PROTOCOL)
                msg.set_metadata("performative", REQUEST_PERFORMATIVE)
                msg.body = json.dumps(content)
                await self.send(msg)
            logger.info(
                "Customer {} asked for a transport to {}.".format(
                    self.agent.name, self.agent.dest
                )
            )
        else:
            logger.warning("Customer {} has no fleet managers.".format(self.agent.name))

    async def accept_transport(self, transport_id):
        """
        Sends a ``spade.message.Message`` to a transport to accept a travel proposal.
        It uses the REQUEST_PROTOCOL and the ACCEPT_PERFORMATIVE.

        Args:
            transport_id (str): The Agent JID of the transport
        """
        reply = Message()
        reply.to = str(transport_id)
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", ACCEPT_PERFORMATIVE)
        content = {
            "customer_id": str(self.agent.jid),
            "origin": self.agent.get("current_pos"),
            "dest": self.agent.dest,
        }
        reply.body = json.dumps(content)
        await self.send(reply)
        self.agent.transport_assigned = str(transport_id)
        logger.info(
            "Customer {} accepted proposal from transport {}".format(
                self.agent.name, transport_id
            )
        )

    async def refuse_transport(self, transport_id):
        """
        Sends an ``spade.message.Message`` to a transport to refuse a travel proposal.
        It uses the REQUEST_PROTOCOL and the REFUSE_PERFORMATIVE.

        Args:
            transport_id (str): The Agent JID of the transport
        """
        reply = Message()
        reply.to = str(transport_id)
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", REFUSE_PERFORMATIVE)
        content = {
            "customer_id": str(self.agent.jid),
            "origin": self.agent.get("current_pos"),
            "dest": self.agent.dest,
        }
        reply.body = json.dumps(content)

        await self.send(reply)
        logger.info(
            "Customer {} refused proposal from transport {}".format(
                self.agent.name, transport_id
            )
        )

    async def run(self):
        raise NotImplementedError
