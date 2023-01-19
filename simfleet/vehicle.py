import asyncio
import json
import time
from asyncio import CancelledError
from collections import defaultdict

from loguru import logger
from spade.agent import Agent
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour
from spade.message import Message
from spade.template import Template

from .movable import MovableMixin, MovingBehaviour
from .geolocated_agent import GeoLocatedAgent

from .helpers import (
    random_position,
    distance_in_meters,
    kmh_to_ms,
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
    VEHICLE_MOVING_TO_DESTINATION,
    VEHICLE_WAITING,
    chunk_path,
    request_path,
    StrategyBehaviour,
    TRANSPORT_NEEDS_CHARGING,
)

MIN_AUTONOMY = 2
ONESECOND_IN_MS = 1000

# esto realmente excepto toda la funcionalidad del cliente es vehicle
class VehicleAgent(MovableMixin, GeoLocatedAgent):
    def __init__(self, agentjid, password):
        GeoLocatedAgent.__init__(self, agentjid, password)
        MovableMixin.__init__(self)
        self.status = VEHICLE_WAITING
        self.set("speed_in_kmh", None)
        
        self.fleetmanager_id = None
        self.registration = None

    async def setup(self):
        try:
            template = Template()
            template.set_metadata("protocol", REGISTER_PROTOCOL)
            register_behaviour = RegistrationBehaviour()
            self.add_behaviour(register_behaviour, template)
            while not self.has_behaviour(register_behaviour):
                logger.warning(
                    "Vehicle {} could not create RegisterBehaviour. Retrying...".format(
                        self.agent_id
                    )
                )
                self.add_behaviour(register_behaviour, template)
            self.ready = True
        except Exception as e:
            logger.error(
                "EXCEPTION creating RegisterBehaviour in Vehicle {}: {}".format(
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

    def set_registration(self, status, content=None):
        """
        Sets the status of registration
        Args:
            status (boolean): True if the transport agent has registered or False if not
            content (dict):
        """
        if content is not None:
            self.icon = content["icon"] if self.icon is None else self.icon
            self.fleet_type = content["fleet_type"]
        self.registration = status

    async def request_access_station(self):

        reply = Message()
        reply.to = self.get("current_station")
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", ACCEPT_PERFORMATIVE)
        logger.debug(
            "{} requesting access to {}".format(
                self.name, self.get("current_station"), reply.body
            )
        )
        await self.send(reply)

        # time waiting in station queue update
        self.waiting_in_queue_time = time.time()

        # WAIT FOR EXPLICIT CONFIRMATION THAT IT CAN CHARGE
        # while True:
        #     msg = await self.receive(timeout=5)
        #     if msg:
        #         performative = msg.get_metadata("performative")
        #         if performative == ACCEPT_PERFORMATIVE:
        #             await self.begin_charging()

    # se usa el metodo de geolocated, la funcion original de transport se divide entre vehicle y transport
    async def set_position(self, coords=None):
        """
        Sets the position of the transport. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        super().set_position(coords)
        if self.is_in_destination():
            logger.info(
                "Transport {} has arrived to destination. Status: {}".format(
                    self.agent_id, self.status
                )
            )
            if self.status == TRANSPORT_MOVING_TO_STATION:
                await self.arrived_to_station()
            else:
                await self.arrived_to_destination()

    def set_speed(self, speed_in_kmh):
        """
        Sets the speed of the transport.

        Args:
            speed_in_kmh (float): the speed of the transport in km per hour
        """
        self.set("speed_in_kmh", speed_in_kmh)

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

class VehicleStrategyBehaviour(StrategyBehaviour):
    """
    Class from which to inherit to create a vehicle strategy.
    You must overload the ```run`` coroutine

    Helper functions:
        * ``pick_up_customer``
        * ``send_proposal``
        * ``cancel_proposal``
    """

    async def on_start(self):
        logger.debug(
            "Strategy {} started in vehicle {}".format(
                type(self).__name__, self.agent.name
            )
        )
        # self.agent.total_waiting_time = 0.0

    async def go_to(self, dest):
        """
        Starts a TRAVEL_PROTOCOL to pick up a customer and get him to his destination.
        It automatically launches all the travelling process until the customer is
        delivered. This travelling process includes to update the transport coordinates as it
        moves along the path at the specified speed.

        Args:
            dest (list): the coordinates of the target destination of the customer
        """
        
        logger.info(
            "Transport {} on route to position {}".format(self.agent.name, dest)
        )
        try:
            await self.agent.move_to(dest)
        except AlreadyInDestination:
            await self.agent.arrived_to_destination()
        except PathRequestException as e:
            logger.error(
                "Raising PathRequestException in go_to for {}".format(
                    self.agent.name
                )
            )
            raise e

    async def run(self):
        raise NotImplementedError
        