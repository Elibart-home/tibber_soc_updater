"""Constants for the Tibber GraphAPI integration."""

DOMAIN = "tibber_soc_updater"
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_VEHICLE_INDEX = 0

# Configuration
CONF_VEHICLE_INDEX = "vehicle_index"

# Sensor attributes
ATTR_VEHICLE_ID = "vehicle_id"
ATTR_HOME_ID = "home_id"
ATTR_BATTERY_LEVEL = "battery_level"
ATTR_RANGE = "range"
ATTR_CHARGING = "charging"
ATTR_CHARGING_POWER = "charging_power"
ATTR_CONNECTED = "connected"

# GraphQL Queries
QUERY_GET_VEHICLE = """
query GetVehicle($homeId: ID!) {
    me {
        home(id: $homeId) {
            id
            vehicles {
                id
                batteryLevel
                range
                connected
                charging
                chargingPower
            }
        }
    }
}
"""

QUERY_GET_CONNECTED_VEHICLE = """
query GetConnectedVehicle($homeId: ID!) {
    me {
        home(id: $homeId) {
            id
            vehicles {
                id
                batteryLevel
                range
                connected
                charging
                chargingPower
                name
                model
            }
        }
    }
}
"""

MUTATION_SET_VEHICLE_SOC = """
mutation SetVehicleSettings($vehicleId: String!, $homeId: String!, $settings: [SettingsItemInput!]) {
    me {
        setVehicleSettings(id: $vehicleId, homeId: $homeId, settings: $settings) {
            __typename
        }
    }
}
""" 