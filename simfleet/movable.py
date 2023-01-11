
from asyncio.log import logger
from simfleet.helpers import AlreadyInDestination, PathRequestException, distance_in_meters, kmh_to_ms
from spade.behaviour import PeriodicBehaviour
from simfleet.utils import chunk_path, request_path

ONESECOND_IN_MS = 1000

class MovableMixin():

    pass
