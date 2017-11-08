import logging

from spade.Agent import Agent

from utils import TAXI_WAITING, random_position, unused_port, request_path

logger = logging.getLogger("TaxiAgent")


class TaxiAgent(Agent):

    def __init__(self, agentjid, password, debug):
        Agent.__init__(self, agentjid, password, debug=debug)
        self.agent_id = None
        self.status = TAXI_WAITING
        self.current_pos = None
        self.dest = None
        self.path = None
        self.distance = 0
        self.duration = 0
        self.port = None

    def _setup(self):
        self.port = unused_port("127.0.0.1")
        self.wui.setPort(self.port)
        self.wui.start()

        self.wui.registerController("update_position", self.update_position_controller)

    def update_position_controller(self, lat, lon):
        self.current_pos = [float(lat), float(lon)]
        logger.info("Agent {} updated position to {}".format(self.agent_id, self.current_pos))
        return None, {}

    def set_id(self, agent_id):
        self.agent_id = agent_id

    def set_position(self, coords=None):
        if coords:
            self.current_pos = coords
        else:
            self.current_pos = random_position()

    def to_json(self):
        return {
            "id": self.agent_id,
            "position": self.current_pos,
            "dest": self.dest,
            "status": self.status,
            "path": self.path,
            "url": "http://127.0.0.1:{port}".format(port=self.port)
        }

    def move_to(self, dest):
        logger.info("Requesting path from {} to {}".format(self.current_pos, dest))
        path, distance, duration = request_path(self.current_pos, dest)
        self.path = path
        self.dest = dest
        self.distance += distance
        self.duration += duration
