import asyncio
import json
import time
from asyncio import CancelledError

from loguru import logger
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from spade.template import Template

from .vehicle import VehicleAgent

from .helpers import (
    distance_in_meters,
    PathRequestException,
    AlreadyInDestination,
)
from .protocol import (
    REQUEST_PROTOCOL,
    TRAVEL_PROTOCOL,
    PROPOSE_PERFORMATIVE,
    CANCEL_PERFORMATIVE,
    INFORM_PERFORMATIVE,
    REGISTER_PROTOCOL,
    REQUEST_PERFORMATIVE,
    ACCEPT_PERFORMATIVE,
    REFUSE_PERFORMATIVE,
    QUERY_PROTOCOL,
)
from .utils import (
    TRANSPORT_WAITING,
    TRANSPORT_MOVING_TO_CUSTOMER,
    TRANSPORT_IN_CUSTOMER_PLACE,
    TRANSPORT_MOVING_TO_DESTINATION,
    TRANSPORT_IN_STATION_PLACE,
    TRANSPORT_CHARGING,
    CUSTOMER_IN_DEST,
    CUSTOMER_LOCATION,
    TRANSPORT_MOVING_TO_STATION,
    chunk_path,
    request_path,
    StrategyBehaviour,
    TRANSPORT_NEEDS_CHARGING,
)

MIN_AUTONOMY = 2
ONESECOND_IN_MS = 1000

# esto realmente excepto toda la funcionalidad del cliente es vehicle
class NewTransportAgent(VehicleAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.status = TRANSPORT_WAITING
        self.set("current_customer", None)

        self.max_capacity = 1
        # self.set("customers", {})
        # customers:
        # {
        #   customer1:{
        #       inside: True/False,
        #       destination(s): [x, y] / [[x1,y1], [x2,y2], ... , [xn,yn]]
        #   }, 
        #   ..., 
        #   customer_n:{...}
        # }

        # cuando llegue a un destino, comprueba que cliente tiene, y le manda un mensaje (subo/bajo del vehiculo, ok, etc)

        # estos 3 en principio se irian
        self.current_customer_orig = None
        self.current_customer_dest = None
        self.set("customer_in_transport", None)

        self.num_assignments = 0
        self.fleetmanager_id = None

        self.request = "station"
        self.stations = None

        # todo esto esta en strategies_fsm
        # Customer in transport event
        self.customer_in_transport_event = asyncio.Event(loop=self.loop)

        def customer_in_transport_callback(old, new):
            # if event flag is False and new is None
            if not self.customer_in_transport_event.is_set() and new is None:
                # Sets event flag to True, all coroutines waiting for it are awakened
                self.customer_in_transport_event.set()

        self.customer_in_transport_callback = customer_in_transport_callback

    async def setup(self):
        try:
            template = Template()
            template.set_metadata("protocol", REGISTER_PROTOCOL)
            register_behaviour = RegistrationBehaviour()
            self.add_behaviour(register_behaviour, template)
            while not self.has_behaviour(register_behaviour):
                logger.warning(
                    "Transport {} could not create RegisterBehaviour. Retrying...".format(
                        self.agent_id
                    )
                )
                self.add_behaviour(register_behaviour, template)
            self.ready = True
        except Exception as e:
            logger.error(
                "EXCEPTION creating RegisterBehaviour in Transport {}: {}".format(
                    self.agent_id, e
                )
            )

    def run_strategy(self):
        """
        Sets the strategy for the transport agent.

        Args:
            strategy_class (``TransportStrategyBehaviour``): The class to be used. Must inherit from ``TransportStrategyBehaviour``
        """
        if not self.running_strategy:
            template1 = Template()
            template1.set_metadata("protocol", REQUEST_PROTOCOL)
            template2 = Template()
            template2.set_metadata("protocol", QUERY_PROTOCOL)
            self.add_behaviour(self.strategy(), template1 | template2)
            self.running_strategy = True

    def set_fleetmanager(self, fleetmanager_id):
        """
        Sets the fleetmanager JID address
        Args:
            fleetmanager_id (str): the fleetmanager jid

        """
        logger.info(
            "Setting fleet {} for agent {}".format(
                fleetmanager_id.split("@")[0], self.name
            )
        )
        self.fleetmanager_id = fleetmanager_id

    def set_route_host(self, route_host):
        """
        Sets the route host server address
        Args:
            route_host (str): route host server address

        """
        self.route_host = route_host

    def is_customer_in_transport(self):
        return self.get("customer_in_transport") is not None

    def is_free(self):
        return self.get("current_customer") is None

    async def arrived_to_destination(self):
        """
        Informs that the transport has arrived to its destination.
        It recomputes the new destination and path if picking up a customer
        or drops it and goes to WAITING status again.
        """
        super().arrived_to_destination()

        if (
            not self.is_customer_in_transport()
        ):  # self.status == TRANSPORT_MOVING_TO_CUSTOMER:
            try:
                self.set("customer_in_transport", self.get("current_customer"))
                # self.add_target_position(self.current_customer_dest)
                await self.move_to_next_destination()
            except PathRequestException:
                await self.cancel_customer()
                self.status = TRANSPORT_WAITING
            except AlreadyInDestination:
                await self.drop_customer()
            else:
                await self.inform_customer(TRANSPORT_IN_CUSTOMER_PLACE)
                self.status = TRANSPORT_MOVING_TO_DESTINATION
                logger.info(
                    "Transport {} has picked up the customer {}.".format(
                        self.agent_id, self.get("current_customer")
                    )
                )
        else:  # elif self.status == TRANSPORT_MOVING_TO_DESTINATION:
            logger.error(
                    "Transport {} is still moving.".format(
                        self.agent_id
                    )
                )
            await self.drop_customer()

    async def drop_customer(self):
        """
        Drops the customer that the transport is carring in the current location.
        """
        await self.inform_customer(CUSTOMER_IN_DEST)
        self.status = TRANSPORT_WAITING
        logger.debug(
            "Transport {} has dropped the customer {} in destination.".format(
                self.agent_id, self.get("current_customer")
            )
        )
        self.set("current_customer", None)
        self.set("customer_in_transport", None)

    async def drop_station(self):
        """
        Drops the customer that the transport is carring in the current location.
        """
        # data = {"status": TRANSPORT_LOADED}
        # await self.inform_station(data)
        self.status = TRANSPORT_WAITING
        logger.debug(
            "Transport {} has dropped the station {}.".format(
                self.agent_id, self.get("current_station")
            )
        )
        self.set("current_station", None)

    async def inform_customer(self, status, data=None):
        """
        Sends a message to the current assigned customer to inform her about a new status.

        Args:
            status (int): The new status code
            data (dict, optional): complementary info about the status
        """
        if data is None:
            data = {}
        msg = Message()
        msg.to = self.get("current_customer")
        msg.set_metadata("protocol", TRAVEL_PROTOCOL)
        msg.set_metadata("performative", INFORM_PERFORMATIVE)
        data["status"] = status
        msg.body = json.dumps(data)
        await self.send(msg)

    async def cancel_customer(self, data=None):
        """
        Sends a message to the current assigned customer to cancel the assignment.

        Args:
            data (dict, optional): Complementary info about the cancellation
        """
        logger.error(
            "Transport {} could not get a path to customer {}.".format(
                self.agent_id, self.get("current_customer")
            )
        )
        if data is None:
            data = {}
        reply = Message()
        reply.to = self.get("current_customer")
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", CANCEL_PERFORMATIVE)
        reply.body = json.dumps(data)
        logger.debug(
            "Transport {} sent cancel proposal to customer {}".format(
                self.agent_id, self.get("current_customer")
            )
        )
        await self.send(reply)

    # se usa el metodo de geolocated, aparte de la funcionalidad de avisar al customer, que es algo solo del transporte
    async def set_position(self, coords=None):
        """
        Sets the position of the transport. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        
        super().set_position(coords)
        self.set("current_pos", coords) 

        if self.status == TRANSPORT_MOVING_TO_DESTINATION:
            await self.inform_customer(
                CUSTOMER_LOCATION, {"location": self.get("current_pos")} 
            )
        if self.is_in_destination():
            logger.info(
                "Transport {} has arrived to destination. Status: {}".format(
                    self.agent_id, self.status
                )
            )
            await self.arrived_to_destination()
            

    def to_json(self):
        """
        Serializes the main information of a transport agent to a JSON format.
        It includes the id of the agent, its current position, the destination coordinates of the agent,
        the current status, the speed of the transport (in km/h), the path it is following (if any), the customer that it
        has assigned (if any), the number of assignments if has done and the distance that the transport has traveled.

        Returns:
            dict: a JSON doc with the main information of the transport.

            Example::

                {
                    "id": "cphillips",
                    "position": [ 39.461327, -0.361839 ],
                    "dest": [ 39.460599, -0.335041 ],
                    "status": 24,
                    "speed": 1000,
                    "path": [[0,0], [0,1], [1,0], [1,1], ...],
                    "customer": "ghiggins@127.0.0.1",
                    "assignments": 2,
                    "distance": 3481.34
                }
        """
        data = super().to_json()
        data.update({
            "assignments": self.num_assignments,
            "customer": self.get("current_customer").split("@")[0]
            if self.get("current_customer")
            else None,
            "service": self.fleet_type,
            "fleet": self.fleetmanager_id.split("@")[0],
            "speed": float("{0:.2f}".format(self.animation_speed))
            if self.animation_speed
            else None,
            "path": self.get("path"),
        })
        return data


class RegistrationBehaviour(CyclicBehaviour):
    async def on_start(self):
        logger.debug("Strategy {} started in transport".format(type(self).__name__))

    async def send_registration(self):
        """
        Send a ``spade.message.Message`` with a proposal to manager to register.
        """
        logger.debug(
            "Transport {} sent proposal to register to manager {}".format(
                self.agent.name, self.agent.fleetmanager_id
            )
        )
        content = {
            "name": self.agent.name,
            "jid": str(self.agent.jid),
            "fleet_type": self.agent.fleet_type,
        }
        msg = Message()
        msg.to = str(self.agent.fleetmanager_id)
        msg.set_metadata("protocol", REGISTER_PROTOCOL)
        msg.set_metadata("performative", REQUEST_PERFORMATIVE)
        msg.body = json.dumps(content)
        await self.send(msg)

    async def run(self):
        try:
            if not self.agent.registration:
                await self.send_registration()
            msg = await self.receive(timeout=10)
            if msg:
                performative = msg.get_metadata("performative")
                if performative == ACCEPT_PERFORMATIVE:
                    content = json.loads(msg.body)
                    self.agent.set_registration(True, content)
                    logger.info(
                        "[{}] Registration in the fleet manager accepted: {}.".format(
                            self.agent.name, self.agent.fleetmanager_id
                        )
                    )
                    self.kill(exit_code="Fleet Registration Accepted")
                elif performative == REFUSE_PERFORMATIVE:
                    logger.warning(
                        "Registration in the fleet manager was rejected (check fleet type)."
                    )
                    self.kill(exit_code="Fleet Registration Rejected")
        except CancelledError:
            logger.debug("Cancelling async tasks...")
        except Exception as e:
            logger.error(
                "EXCEPTION in RegisterBehaviour of Transport {}: {}".format(
                    self.agent.name, e
                )
            )


class TransportStrategyBehaviour(StrategyBehaviour):
    """
    Class from which to inherit to create a transport strategy.
    You must overload the ```run`` coroutine

    Helper functions:
        * ``pick_up_customer``
        * ``send_proposal``
        * ``cancel_proposal``
    """

    async def on_start(self):
        logger.debug(
            "Strategy {} started in transport {}".format(
                type(self).__name__, self.agent.name
            )
        )
        # self.agent.total_waiting_time = 0.0

    async def pick_up_customer(self, customer_id, origin, dest):
        """
        Starts a TRAVEL_PROTOCOL to pick up a customer and get him to his destination.
        It automatically launches all the travelling process until the customer is
        delivered. This travelling process includes to update the transport coordinates as it
        moves along the path at the specified speed.

        Args:
            customer_id (str): the id of the customer
            origin (list): the coordinates of the current location of the customer
            dest (list): the coordinates of the target destination of the customer
        """
        logger.info(
            "Transport {} on route to customer {}".format(self.agent.name, customer_id)
        )
        reply = Message()
        reply.to = customer_id
        reply.set_metadata("performative", INFORM_PERFORMATIVE)
        reply.set_metadata("protocol", TRAVEL_PROTOCOL)
        content = {"status": TRANSPORT_MOVING_TO_CUSTOMER}
        reply.body = json.dumps(content)
        self.set("current_customer", customer_id)
        self.agent.current_customer_orig = origin
        self.agent.current_customer_dest = dest
        
        await self.send(reply)
        self.agent.num_assignments += 1
        
        try:
            self.agent.add_target_position(self.agent.current_customer_orig)
            self.agent.add_target_position(self.agent.current_customer_dest)
            logger.success(
            "Transport {} has {} destinations".format(
                    self.agent.name, self.agent.get("destinations")
                )
            )
            await self.agent.move_to_next_destination()
        except AlreadyInDestination:
            logger.error(
                "Transport {} is already in destination".format(
                    self.agent.name
                )
            )
            await self.agent.arrived_to_destination()
        except PathRequestException as e:
            logger.error(
                "Raising PathRequestException in pick_up_customer for {}".format(
                    self.agent.name
                )
            )
            raise e

    async def send_proposal(self, customer_id, content=None):
        """
        Send a ``spade.message.Message`` with a proposal to a customer to pick up him.
        If the content is empty the proposal is sent without content.

        Args:
            customer_id (str): the id of the customer
            content (dict, optional): the optional content of the message
        """
        if content is None:
            content = {}
        logger.info(
            "Transport {} sent proposal to {}".format(self.agent.name, customer_id)
        )
        reply = Message()
        reply.to = customer_id
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", PROPOSE_PERFORMATIVE)
        reply.body = json.dumps(content)
        await self.send(reply)

    async def cancel_proposal(self, customer_id, content=None):
        """
        Send a ``spade.message.Message`` to cancel a proposal.
        If the content is empty the proposal is sent without content.

        Args:
            customer_id (str): the id of the customer
            content (dict, optional): the optional content of the message
        """
        if content is None:
            content = {}
        logger.info(
            "Transport {} sent cancel proposal to customer {}".format(
                self.agent.name, customer_id
            )
        )
        reply = Message()
        reply.to = customer_id
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", CANCEL_PERFORMATIVE)
        reply.body = json.dumps(content)
        await self.send(reply)

    async def run(self):
        raise NotImplementedError
