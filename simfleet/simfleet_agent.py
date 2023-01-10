import json
import time
from asyncio import CancelledError

from spade.agent import Agent

from .helpers import random_position



class SimfleetAgent(Agent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.agent_id = None
        self.strategy = None
        self.running_strategy = False
        self.port = None
        self.init_time = None
        self.end_time = None
        self.stopped = True
        self.ready = False
        self.is_launched = False
        self.directory_id = None

    def is_ready(self):
        return not self.is_launched or (self.is_launched and self.ready)

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