import json
import time
from asyncio import CancelledError

from loguru import logger
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, State, FSMBehaviour
from spade.message import Message
from spade.template import Template

from .helpers import random_position
from .protocol import (
    REQUEST_PROTOCOL,
    TRAVEL_PROTOCOL,
    REQUEST_PERFORMATIVE,
    ACCEPT_PERFORMATIVE,
    REFUSE_PERFORMATIVE,
    QUERY_PROTOCOL,
)


class SimfleetAgent(Agent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.agent_id = None
        self.strategy = None
        self.running_strategy = False
        self.port = None
        self.init_time = None
        self.end_time = None
        self.stopped = False
        self.ready = False
        self.is_launched = False
        self.directory_id = None
    
    async def setup(self):
        try:
            fsm = GeneralFSMBehaviour()
            fsm.add_state(name=STATE_ONE, state=StateOne(), initial=True)
            fsm.add_state(name=STATE_TWO, state=StateTwo())
            fsm.add_transition(source=STATE_ONE, dest=STATE_TWO)
            self.add_behaviour(fsm)
            self.ready = True
        except Exception as e:
            logger.error(
                "EXCEPTION creating RegisterBehaviour in Transport {}: {}".format(
                    self.agent_id, e
                )
            )

    def is_ready(self):
        return not self.is_launched or (self.is_launched and self.ready)

    def run_strategy(self):
        """import json
        Runs the strategy for the customer agent.
        """
        if not self.running_strategy:
            fsm = GeneralFSMBehaviour()
            fsm.add_state(name=STATE_ONE, state=StateOne(), initial=True)
            fsm.add_state(name=STATE_TWO, state=StateTwo())
            fsm.add_transition(source=STATE_ONE, dest=STATE_TWO)
            self.add_behaviour(fsm)
            self.running_strategy = True

    def set_id(self, agent_id):
        """
        Sets the agent identifier
        Args:
            agent_id (str): The new Agent Id
        """
        self.agent_id = agent_id

    def set_directory(self, directory_id):
        """
        Sets the directory JID address
        Args:
            directory_id (str): the DirectoryAgent jid

        """
        self.directory_id = directory_id

    def total_time(self):
        """
        Returns the time since the customer was activated until it reached its destination.

        Returns:
            float: the total time of the customer's simulation.
        """
        if self.init_time and self.end_time:
            return self.end_time - self.init_time
        else:
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
        return {
            "id": self.agent_id,
        }


class GeneralFSMBehaviour(FSMBehaviour):
    async def on_start(self):
        print(f"Generic agent starting at initial state {self.current_state}")
        pass

    async def on_end(self):
        # print(f"FSM finished at state {self.current_state}")
        await self.agent.stop()

STATE_ONE = "STATE_ONE"
STATE_TWO = "STATE_TWO"

class StateOne(State):
    async def run(self):
        # print("I'm at state one ")
        self.set_next_state(STATE_TWO)


class StateTwo(State):
    async def run(self):
        # print("I'm at state two (final state)")
        pass
