from asyncio import sleep
import json

from loguru import logger

# from .vehicle import VehicleStrategyBehaviour

# from .customer import CustomerStrategyBehaviour
from .new_customer import CustomerStrategyBehaviour

# from .fleetmanager import FleetManagerStrategyBehaviour
from .new_fleetmanager import FleetManagerStrategyBehaviour

# from .transport import TransportStrategyBehaviour
from .new_transport import TransportStrategyBehaviour

from .helpers import PathRequestException
from .protocol import (
    REQUEST_PERFORMATIVE,
    ACCEPT_PERFORMATIVE,
    REFUSE_PERFORMATIVE,
    PROPOSE_PERFORMATIVE,
    CANCEL_PERFORMATIVE,
    INFORM_PERFORMATIVE,
    QUERY_PROTOCOL,
    REQUEST_PROTOCOL,
)

from .utils import (
    TRANSPORT_WAITING,
    TRANSPORT_WAITING_FOR_APPROVAL,
    CUSTOMER_WAITING,
    TRANSPORT_MOVING_TO_CUSTOMER,
    CUSTOMER_ASSIGNED,
    TRANSPORT_MOVING_TO_STATION,
    TRANSPORT_CHARGING,
    TRANSPORT_CHARGED,
    TRANSPORT_NEEDS_CHARGING,
    TRANSPORT_IN_STATION_PLACE,
)


################################################################
#                                                              #
#                     FleetManager Strategy                    #
#                                                              #
################################################################
class DelegateRequestBehaviour(FleetManagerStrategyBehaviour):
    """
    The default strategy for the FleetManager agent. By default it delegates all requests to all transports.
    """

    async def run(self):
        if not self.agent.registration:
            await self.send_registration()

        msg = await self.receive(timeout=5)
        logger.debug("Manager received message: {}".format(msg))
        if msg:
            for transport in self.get_transport_agents().values():
                msg.to = str(transport["jid"])
                logger.debug(
                    "Manager sent request to transport {}".format(transport["name"])
                )
                await self.send(msg)


################################################################
#                                                              #
#                     Transport Strategy                       #
#                                                              #
################################################################
class AcceptAlwaysStrategyBehaviour(TransportStrategyBehaviour):
    """
    The default strategy for the Transport agent. By default it accepts every request it receives if available.
    """

    async def run(self):
        msg = await self.receive(timeout=5)
        if not msg:
            return
        logger.debug("Transport received message: {}".format(msg))
        try:
            content = json.loads(msg.body)
        except TypeError:
            content = {}

        performative = msg.get_metadata("performative")
        protocol = msg.get_metadata("protocol")

        if protocol == REQUEST_PROTOCOL:
            logger.debug(
                "Transport {} received request protocol from customer/station.".format(
                    self.agent.name
                )
            )

            if performative == REQUEST_PERFORMATIVE:
                if self.agent.status == TRANSPORT_WAITING:
                    await self.send_proposal(content["customer_id"], {})
                    self.agent.status = TRANSPORT_WAITING_FOR_APPROVAL

            elif performative == ACCEPT_PERFORMATIVE:
                if self.agent.status == TRANSPORT_WAITING_FOR_APPROVAL:
                    logger.debug(
                        "Transport {} got accept from {}".format(
                            self.agent.name, content["customer_id"]
                        )
                    )
                    try:
                        self.agent.status = TRANSPORT_MOVING_TO_CUSTOMER
                        await self.pick_up_customer(
                            content["customer_id"], content["origin"], content["dest"]
                        )
                    except PathRequestException:
                        logger.error(
                            "Transport {} could not get a path to customer {}. Cancelling...".format(
                                self.agent.name, content["customer_id"]
                            )
                        )
                        self.agent.status = TRANSPORT_WAITING
                        await self.cancel_proposal(content["customer_id"])
                    except Exception as e:
                        logger.error(
                            "Unexpected error in transport {}: {}".format(
                                self.agent.name, e
                            )
                        )
                        await self.cancel_proposal(content["customer_id"])
                        self.agent.status = TRANSPORT_WAITING
                else:
                    await self.cancel_proposal(content["customer_id"])

            elif performative == REFUSE_PERFORMATIVE:
                logger.debug(
                    "Transport {} got refusal from customer/station".format(
                        self.agent.name
                    )
                )
                self.agent.status = TRANSPORT_WAITING

            elif performative == CANCEL_PERFORMATIVE:
                logger.info(
                    "Cancellation of request for {} information".format(
                        self.agent.fleet_type
                    )
                )


################################################################
#                                                              #
#                       Customer Strategy                      #
#                                                              #
################################################################
class AcceptFirstRequestBehaviour(CustomerStrategyBehaviour):
    """
    The default strategy for the Customer agent. By default it accepts the first proposal it receives.
    """

    async def run(self):
        if self.agent.fleetmanagers is None:
            await self.send_get_managers(self.agent.fleet_type)

            msg = await self.receive(timeout=5)
            if msg:
                performative = msg.get_metadata("performative")
                if performative == INFORM_PERFORMATIVE:
                    self.agent.fleetmanagers = json.loads(msg.body)
                    return
                elif performative == CANCEL_PERFORMATIVE:
                    logger.info(
                        "Cancellation of request for {} information".format(
                            self.agent.fleet_type
                        )
                    )
                    return

        if self.agent.status == CUSTOMER_WAITING:
            await self.send_request(content={})

        msg = await self.receive(timeout=5)

        if msg:
            performative = msg.get_metadata("performative")
            transport_id = msg.sender
            if performative == PROPOSE_PERFORMATIVE:
                if self.agent.status == CUSTOMER_WAITING:
                    logger.debug(
                        "Customer {} received proposal from transport {}".format(
                            self.agent.name, transport_id
                        )
                    )
                    await self.accept_transport(transport_id)
                    self.agent.status = CUSTOMER_ASSIGNED
                else:
                    await self.refuse_transport(transport_id)

            elif performative == CANCEL_PERFORMATIVE:
                if self.agent.transport_assigned == str(transport_id):
                    logger.warning(
                        "Customer {} received a CANCEL from Transport {}.".format(
                            self.agent.name, transport_id
                        )
                    )
                    self.agent.status = CUSTOMER_WAITING


