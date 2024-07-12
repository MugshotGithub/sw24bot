import json
import os
from typing import Any

from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from dotenv import load_dotenv  # Python-dotenv package

load_dotenv()

# Select your transport with a defined url endpoint
transport = AIOHTTPTransport(url="https://api.start.gg/gql/alpha",
                             headers={"Authorization": f"Bearer {os.getenv("STARTGG_KEY")}"})

# Create a GraphQL client using the defined transport
client = Client(transport=transport, fetch_schema_from_transport=True)

# Provide a GraphQL query
eventIdQuery = gql(
    """
    query TournamentQuery($slug: String) {
            tournament(slug: $slug){
                name
                events {
                    id
                    name
                }
            }
        }
    """
)

eventInfoQuery = gql(
    """
    query EventQuery($id: ID!) {
        event(id: $id){
            id
            name
            phases {
                name
                phaseGroups {
                    nodes {
                        displayIdentifier
                        sets {
                            nodes {
                                id
                                fullRoundText
                                state
                                identifier
                                slots {
                                    standing {
                                        stats {
                                            score {
                                                value
                                            }
                                        }
                                    }
                                    entrant {
                                        id
                                        name
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """
)


# This is a Generator
# This should be used in a for loop.
async def get_games(tournament):
    try:
        # Execute the query on the transport
        tournament = await get_tournament_info(tournament)
    except Exception:
        yield None
    if tournament is None:
        yield None

    try:
        for event in tournament["events"]:
            pools = ["", "Pool 1", "Pool 2", "Pool 3", "Pool 4", "Pool 5", "Pool 6"]
            eventInfo = await client.execute_async(eventInfoQuery, variable_values={"id": event["id"]})
            for phase in eventInfo["event"]["phases"]:
                for phaseGroup in phase["phaseGroups"]["nodes"]:
                    for gameSet in phaseGroup["sets"]["nodes"]:
                        fullPhaseName = event["name"] + (phase["name"] if len(phase["phaseGroups"]["nodes"]) < 2 else f"{phase["name"]} {pools[int(phaseGroup["displayIdentifier"])]}")
                        gameSet["id"] = fullPhaseName.replace(" ", "").replace("+", "plus").replace(":", "") + gameSet["identifier"]

            yield eventInfo["event"]
    except Exception:
        yield None


async def get_tournament_info(tournament):
    try:
        result = await client.execute_async(eventIdQuery, variable_values={"slug": tournament})
        return result["tournament"]
    except Exception:
        return None



