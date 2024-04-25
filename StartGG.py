import json
import os
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
            phases {
                name
                phaseGroups {
                    nodes {
                        displayIdentifier
                        sets {
                            nodes {
                                id
                                fullRoundText
                                slots {
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
async def get_games():
    # Execute the query on the transport
    result = client.execute(eventIdQuery, variable_values={"slug": 'tournament/second-wind-2024'})

    tournament = result["tournament"]
    for event in tournament["events"]:
        eventInfo = client.execute(eventInfoQuery, variable_values={"id": event["id"]})
        yield eventInfo["event"]