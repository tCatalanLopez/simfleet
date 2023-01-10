import json
import time
from asyncio import CancelledError

from loguru import logger
from .simfleet_agent import SimfleetAgent

from .helpers import random_position



class GeoLocatedAgent(SimfleetAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.current_pos = None
        self.dest = None
        self.icon = None
    
    def set_icon(self, icon):
        self.icon = icon

    def set_position(self, coords=None):
        """
        Sets the position of the Agent. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        if coords:
            self.current_pos = coords
        else:
            self.current_pos = random_position()
        logger.debug(
            "Agent {} position is {}".format(self.agent_id, self.current_pos)
        )

    def get_position(self):
        """
        Returns the current position of the Agent.

        Returns:
            list: the coordinates of the current position of the Agent (lon, lat)
        """
        return self.current_pos

    def set_target_position(self, coords=None):
        """
        Sets the target position of the Agent (i.e. its destination).
        If no position is provided the destination is setted to a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        if coords:
            self.dest = coords
        else:
            self.dest = random_position()
        logger.debug(
            "Agent {} target position is {}".format(self.agent_id, self.dest)
        )
