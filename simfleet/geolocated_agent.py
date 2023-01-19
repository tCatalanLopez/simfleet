import json
import time
from asyncio import CancelledError

from loguru import logger
from .simfleet_agent import SimfleetAgent

from .helpers import random_position



class GeoLocatedAgent(SimfleetAgent):
    def __init__(self, agentjid, password):
        super().__init__(agentjid, password)
        self.route_host = None
        self.set("current_pos", None)

        self.icon = None
    
    def set_icon(self, icon):
        self.icon = icon

    def set_route_host(self, route_host):
        """
        Sets the route host server address
        Args:
            route_host (str): the route host server address

        """
        self.route_host = route_host

    def set_position(self, coords=None):
        """
        Sets the position of the Agent. If no position is provided it is located in a random position.

        Args:
            coords (list): a list coordinates (longitude and latitude)
        """
        if coords:
            self.set("current_pos", coords)
        else:
            self.set("current_pos", random_position())
        logger.debug(
            "Agent {} position is {}".format(self.agent_id, self.get("current_pos"))
        )

    def set_initial_position(self, coords):
        self.set("current_pos", coords)

    def get_position(self):
        """
        Returns the current position of the Agent.

        Returns:
            list: the coordinates of the current position of the Agent (lon, lat)
        """
        return self.get("current_pos")

    def to_json(self):
        """
        Returns a JSON with the relevant data of this type of agent
        """
        data = super().to_json()
        data.update({
            "position": [
                float(coord) for coord in self.get("current_pos")
            ],
            "icon": self.icon
        })
        return data
