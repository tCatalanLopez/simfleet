import json
import time
from asyncio import CancelledError

from loguru import logger
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, State, FSMBehaviour
from spade.message import Message
from spade.template import Template
from .simfleet_agent import SimfleetAgent

from .helpers import random_position



class GeoLocatedAgent(SimfleetAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.current_pos = None
        self.icon = None
    
    async def setup(self):
        try: 
            self.add_behaviour(Ciclo())
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
            self.setup()
            self.running_strategy = True

    def set_id(self, agent_id):
        """
        Sets the agent identifier
        Args:
            agent_id (str): The new Agent Id
        """
        self.agent_id = agent_id
    
    def set_icon(self, icon):
        self.icon = icon
    
    def is_ready(self):
        return not self.is_launched or (self.is_launched and self.ready)

    def set_directory(self, directory_id):
        """
        Sets the directory JID address
        Args:
            directory_id (str): the DirectoryAgent jid

        """
        self.directory_id = directory_id

    async def set_position(self, coords=None):
        """
        Sets the position of the transport. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        if coords:
            self.set("current_pos", coords)
        else:
            self.set("current_pos", random_position())

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

class Ciclo(CyclicBehaviour):
    async def on_start(self):
        logger.info("geolocated agent esta empezando")

    async def run(self):
        # logger.info("geolocated agent sigue funcionando")
        pass
    async def on_end(self):
        logger.info("acabo")
        pass