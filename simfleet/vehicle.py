import asyncio
import json
import time
from asyncio import CancelledError
from collections import defaultdict

from loguru import logger
from spade.agent import Agent
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour, State, FSMBehaviour
from spade.message import Message
from spade.template import Template

# from simfleet.strategies_fsm import TD, MovementStep, SelectDestination, UpdateCustomerInfo, VehicleFSM

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
    STATE_MOVEMENT_STEP,
    STATE_SELECT_DESTINATION,
    STATE_TD,
    STATE_UPDATE_CUSTOMER_INFO,
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
        logger.info("Vehicle agent {} running".format(self.name))
        self.ready = True
        
    def run_strategy(self):
        """
        Sets the strategy for the transport agent.

        Args:
            strategy_class (``TransportStrategyBehaviour``): The class to be used. Must inherit from ``TransportStrategyBehaviour``
        """
        if not self.running_strategy:
            fsm = VehicleFSM()
            fsm.add_state(name=STATE_SELECT_DESTINATION, state=SelectDestination(), initial=True)
            fsm.add_state(name=STATE_MOVEMENT_STEP, state=MovementStep())
            fsm.add_state(name=STATE_UPDATE_CUSTOMER_INFO, state=UpdateCustomerInfo())
            fsm.add_state(name=STATE_TD, state=TD())
            fsm.add_transition(source=STATE_SELECT_DESTINATION, dest=STATE_MOVEMENT_STEP)
            fsm.add_transition(source=STATE_MOVEMENT_STEP, dest=STATE_UPDATE_CUSTOMER_INFO)
            fsm.add_transition(source=STATE_UPDATE_CUSTOMER_INFO, dest=STATE_TD)
            fsm.add_transition(source=STATE_TD, dest=STATE_MOVEMENT_STEP)
            fsm.add_transition(source=STATE_TD, dest=STATE_SELECT_DESTINATION)
            fsm.add_transition(source=STATE_SELECT_DESTINATION, dest=STATE_SELECT_DESTINATION)
            fsm.add_transition(source=STATE_MOVEMENT_STEP, dest=STATE_MOVEMENT_STEP)
            self.add_behaviour(fsm)
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

class VehicleFSM(FSMBehaviour):
    async def on_start(self):
        logger.info("-----------------vehicle fsm started-----------------")

    async def on_end(self):
        await self.agent.stop()
    

class SelectDestination( State):
    async def on_start(self):
        logger.info("-----------------selectDestination-----------------")

    async def run(self):
        logger.info("List of destinations of agent {}: {}".format(
            self.agent.name, self.agent.get("destinations")
            )
        )
        
        if self.agent.get("destinations") != []:
            self.set_next_state(STATE_MOVEMENT_STEP)
        else: 
            time.sleep(10)
            self.set_next_state(STATE_SELECT_DESTINATION)

class MovementStep( State):
    async def on_start(self):
        logger.info("-----------------MovementStep-----------------")

    async def run(self):
        try:
            await self.agent.move_to_next_destination()
            self.set_next_state(STATE_MOVEMENT_STEP)
        except AlreadyInDestination:
            logger.success(
                "Vehicle {} is already in destination".format(
                    self.agent.name
                )
            )
            self.agent.arrived_to_destination()
            self.set_next_state(STATE_UPDATE_CUSTOMER_INFO)
        except PathRequestException as e:
            logger.error(
                "Raising PathRequestException in pick_up_customer for {}".format(
                    self.agent.name
                )
            )
            self.set_next_state(STATE_UPDATE_CUSTOMER_INFO)
            raise e

class UpdateCustomerInfo( State):
    async def on_start(self):
        logger.info("-----------------UpdateCustomerInfo-----------------")
        
    async def run(self):
        message_dest = self.agent.get("customers")
        if(str(self.agent.get_position()) in message_dest): 
            for customer in message_dest[str(self.agent.get_position())]:
                reply = Message()
                reply.to = customer
                reply.set_metadata("performative", INFORM_PERFORMATIVE)
                content = {"position":self.agent.get_position()}
                reply.body = json.dumps(content)
                await self.send(reply)
        else:
            # TODO
            # si no existe una lista de clientes asociada a un destino, hay que mirarlo se mandaría un mesaje a si mismo
            
            pass
        self.set_next_state(STATE_TD)

class TD(State):
    async def on_start(self):
        logger.info("-----------------TD-----------------")
        
    async def run(self):
        self.set_next_state(STATE_SELECT_DESTINATION)