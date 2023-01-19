
from asyncio.log import logger
from simfleet.helpers import AlreadyInDestination, PathRequestException, distance_in_meters, kmh_to_ms, random_position
from spade.behaviour import PeriodicBehaviour
from simfleet.utils import chunk_path, request_path

ONESECOND_IN_MS = 1000

class MovableMixin():

    def __init__(self):
        self.set("path", None)
        self.chunked_path = None
        self.animation_speed = ONESECOND_IN_MS
        self.set("speed_in_kmh", 3000)
        self.dest = None
        # self.set(dest, {})?
        # {
        #   destino: [cliente, AccionARealizar]
        # }
        
        self.distances = []
        self.durations = []

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
        behav = MovingBehaviour(period=1)
        self.add_behaviour(behav)

    def is_in_destination(self):
        """
        Checks if the agent has arrived to its destination.

        Returns:
            bool: whether the agent is at its destination or not
        """
        return self.dest == self.get_position()

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
        # logger.debug(
        #     "Agent {} target position is {}".format(self.agent_id, self.dest)
        # )

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

    async def arrived_to_destination(self):
        """
        Informs that the transport has arrived to its destination.
        It recomputes the new destination and path if picking up a customer
        or drops it and goes to WAITING status again.
        """
        self.set("path", None)
        self.chunked_path = None

    def to_json(self):
        """
        Returns a JSON with the relevant data of this type of agent
        """
        data = super().to_json()
        data.update({
            "dest": [float(coord) for coord in self.dest]
            if self.dest
            else None
        })
        return data

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