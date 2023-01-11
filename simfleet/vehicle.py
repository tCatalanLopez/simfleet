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

from simfleet.movable import MovableMixin
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
    chunk_path,
    request_path,
    StrategyBehaviour,
    TRANSPORT_NEEDS_CHARGING,
)

MIN_AUTONOMY = 2
ONESECOND_IN_MS = 1000

# esto realmente excepto toda la funcionalidad del cliente es vehicle
class Vehicle(MovableMixin,GeoLocatedAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.set("path", None)
        self.set("speed_in_kmh", 3000)
        self.fleetmanager_id = None
        self.registration = None

        # a movable
        self.chunked_path = None
        self.animation_speed = ONESECOND_IN_MS
        
        self.distances = []
        self.durations = []
        
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

    async def arrived_to_destination(self):
        """
        Informs that the transport has arrived to its destination.
        It recomputes the new destination and path if picking up a customer
        or drops it and goes to WAITING status again.
        """
        self.set("path", None)
        self.chunked_path = None

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

    # movable mixin
    async def request_path(self, origin, destination):
        """
        Requests a path between two points (origin and destination) using the route server.

        Args:
            origin (list): the coordinates of the origin of the requested path
            destination (list): the coordinates of the end of the requested path

        Returns:
            list, float, float: A list of points that represent the path from origin to destination, the distance and
            the estimated duration

        Examples:
            >>> path, distance, duration = await self.request_path(origin=[0,0], destination=[1,1])
            >>> print(path)
            [[0,0], [0,1], [1,1]]
            >>> print(distance)
            2.0
            >>> print(duration)
            3.24
        """
        return await request_path(self, origin, destination, self.route_host)

    # se usa el metodo de geolocated, aparte de la funcionalidad de avisar al customer, que es algo solo del transporte
    async def set_position(self, coords=None):
        """
        Sets the position of the transport. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        
        super().set_position(coords)

        # esto en principio deberia irse si lo vamos a dejar como self.current_pos
        self.set("current_pos", coords) 
        
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

    def is_in_destination(self):
        """
        Checks if the transport has arrived to its destination.

        Returns:
            bool: whether the transport is at its destination or not
        """
        return self.dest == self.get_position()

    # ChargeableMixin
    def set_km_expense(self, expense=0):
        self.current_autonomy_km -= expense
    
    # ChargeableMixin
    def set_autonomy(self, autonomy, current_autonomy=None):
        self.max_autonomy_km = autonomy
        self.current_autonomy_km = (
            current_autonomy if current_autonomy is not None else autonomy
        )
    
    # ChargeableMixin
    def get_autonomy(self):
        return self.current_autonomy_km

    # ChargeableMixin
    def calculate_km_expense(self, origin, start, dest=None):
        fir_distance = distance_in_meters(origin, start)
        sec_distance = distance_in_meters(start, dest)
        if dest is None:
            sec_distance = 0
        return (fir_distance + sec_distance) // 1000

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
            "fleet_manager": self.fleetmanager_id
        })
        return data

    # todo esto va a movable mixing tal cual
    async def move_to(self, dest):
        """
        Moves the transport to a new destination.

        Args:
            dest (list): the coordinates of the new destination (in lon, lat format)

        Raises:
             AlreadyInDestination: if the transport is already in the destination coordinates.
        """
        if self.get("current_pos") == dest:
            raise AlreadyInDestination
        counter = 5
        path = None
        distance, duration = 0, 0
        while counter > 0 and path is None:
            logger.debug(
                "Requesting path from {} to {}".format(self.get("current_pos"), dest)
            )
            path, distance, duration = await self.request_path(
                self.get("current_pos"), dest
            )
            counter -= 1
        if path is None:
            raise PathRequestException("Error requesting route.")

        self.set("path", path)
        try:
            self.chunked_path = chunk_path(path, self.get("speed_in_kmh"))
        except Exception as e:
            logger.error("Exception chunking path {}: {}".format(path, e))
            raise PathRequestException
        self.dest = dest
        self.distances.append(distance)
        self.durations.append(duration)
        behav = self.MovingBehaviour(period=1)
        self.add_behaviour(behav)

    async def step(self):
        """
        Advances one step in the simulation
        """
        if self.chunked_path:
            _next = self.chunked_path.pop(0)
            distance = distance_in_meters(self.get_position(), _next)
            self.animation_speed = (
                distance / kmh_to_ms(self.get("speed_in_kmh")) * ONESECOND_IN_MS
            )
            await self.set_position(_next)


    async def request_path(self, origin, destination):
        """
        Requests a path between two points (origin and destination) using the route server.

        Args:
            origin (list): the coordinates of the origin of the requested path
            destination (list): the coordinates of the end of the requested path

        Returns:
            list, float, float: A list of points that represent the path from origin to destination, the distance and
            the estimated duration

        Examples:
            >>> path, distance, duration = await self.request_path(origin=[0,0], destination=[1,1])
            >>> print(path)
            [[0,0], [0,1], [1,1]]
            >>> print(distance)
            2.0
            >>> print(duration)
            3.24
        """
        return await request_path(self, origin, destination, self.route_host)

    class MovingBehaviour(PeriodicBehaviour):
            """
            This is the internal behaviour that manages the movement of the transport.
            It is triggered when the transport has a new destination and the periodic tick
            is recomputed at every step to show a fine animation.
            This moving behaviour includes to update the transport coordinates as it
            moves along the path at the specified speed.
            """

            async def run(self):
                await self.agent.step()
                self.period = self.agent.animation_speed / ONESECOND_IN_MS
                if self.agent.is_in_destination():
                    self.agent.remove_behaviour(self)

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
    Class from which to inherit to create a transport strategy.
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

    async def send_confirmation_travel(self, station_id):
        logger.info(
            "Transport {} sent confirmation to station {}".format(
                self.agent.name, station_id
            )
        )
        reply = Message()
        reply.to = station_id
        reply.set_metadata("protocol", REQUEST_PROTOCOL)
        reply.set_metadata("performative", ACCEPT_PERFORMATIVE)
        await self.send(reply)

    async def go_to_the_station(self, station_id, dest):
        """
        Starts a TRAVEL_PROTOCOL to pick up a customer and get him to his destination.
        It automatically launches all the travelling process until the customer is
        delivered. This travelling process includes to update the transport coordinates as it
        moves along the path at the specified speed.

        Args:
            station_id (str): the id of the customer
            dest (list): the coordinates of the target destination of the customer
        """
        logger.info(
            "Transport {} on route to station {}".format(self.agent.name, station_id)
        )
        self.status = TRANSPORT_MOVING_TO_STATION
        reply = Message()
        reply.to = station_id
        reply.set_metadata("performative", INFORM_PERFORMATIVE)
        reply.set_metadata("protocol", TRAVEL_PROTOCOL)
        content = {"status": TRANSPORT_MOVING_TO_STATION}
        reply.body = json.dumps(content)
        self.set("current_station", station_id)
        self.agent.current_station_dest = dest
        await self.send(reply)
        # informs the TravelBehaviour of the station that the transport is coming

        self.agent.num_charges += 1
        travel_km = self.agent.calculate_km_expense(self.get("current_pos"), dest)
        self.agent.set_km_expense(travel_km)
        try:
            logger.debug("{} move_to station {}".format(self.agent.name, station_id))
            await self.agent.move_to(self.agent.current_station_dest)
        except AlreadyInDestination:
            logger.debug(
                "{} is already in the stations' {} position. . .".format(
                    self.agent.name, station_id
                )
            )
            await self.agent.arrived_to_station()

    # chargeableMixin
    def has_enough_autonomy(self, customer_orig, customer_dest):
        autonomy = self.agent.get_autonomy()
        if autonomy <= MIN_AUTONOMY:
            logger.warning(
                "{} has not enough autonomy ({}).".format(self.agent.name, autonomy)
            )
            return False
        travel_km = self.agent.calculate_km_expense(
            self.get("current_pos"), customer_orig, customer_dest
        )
        logger.debug(
            "Transport {} has autonomy {} when max autonomy is {}"
            " and needs {} for the trip".format(
                self.agent.name,
                self.agent.current_autonomy_km,
                self.agent.max_autonomy_km,
                travel_km,
            )
        )

        if autonomy - travel_km < MIN_AUTONOMY:
            logger.warning(
                "{} has not enough autonomy to do travel ({} for {} km).".format(
                    self.agent.name, autonomy, travel_km
                )
            )
            return False
        return True

    # chargeableMixin
    def check_and_decrease_autonomy(self, customer_orig, customer_dest):
        autonomy = self.agent.get_autonomy()
        travel_km = self.agent.calculate_km_expense(
            self.get("current_pos"), customer_orig, customer_dest
        )
        if autonomy - travel_km < MIN_AUTONOMY:
            logger.warning(
                "{} has not enough autonomy to do travel ({} for {} km).".format(
                    self.agent.name, autonomy, travel_km
                )
            )
            return False
        self.agent.set_km_expense(travel_km)
        return True

    async def send_get_stations(self, content=None):

        if content is None or len(content) == 0:
            content = self.agent.request
        msg = Message()
        msg.to = str(self.agent.directory_id)
        msg.set_metadata("protocol", QUERY_PROTOCOL)
        msg.set_metadata("performative", REQUEST_PERFORMATIVE)
        msg.body = content
        await self.send(msg)

        logger.info(
            "Transport {} asked for stations to Directory {} for type {}.".format(
                self.agent.name, self.agent.directory_id, self.agent.request
            )
        )

    # chargeableMixin
    async def charge_allowed(self):
        self.set("in_station_place", None)  # new
        await self.agent.begin_charging()

    async def run(self):
        raise NotImplementedError
