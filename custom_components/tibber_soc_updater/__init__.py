"""The Tibber GraphAPI integration."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
import async_timeout
from datetime import timedelta

# Version information
__version__ = "2.2.0"
__version_info__ = (2, 2, 0)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    MUTATION_SET_VEHICLE_SOC,
    ATTR_VEHICLE_ID,
    ATTR_HOME_ID,
    ATTR_BATTERY_LEVEL,
)

__all__ = ["TibberGraphAPI"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []  # No platforms, service-only integration

# Service-only integration, no config schema needed

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tibber GraphAPI from a config entry."""
    session = async_get_clientsession(hass)
    
    api = TibberGraphAPI(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    
    try:
        await api.authenticate()
    except Exception as err:
        _LOGGER.error("Failed to authenticate with Tibber: %s", err)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    # Register service
    async def set_vehicle_soc(call: ServiceCall) -> None:
        """Set vehicle state of charge."""
        _LOGGER.debug("Service called with data: %s", call.data)
        
        vehicle_id = call.data.get(ATTR_VEHICLE_ID) or call.data.get("vehicle_id")
        home_id = call.data.get(ATTR_HOME_ID) or call.data.get("home_id")
        battery_level = call.data.get(ATTR_BATTERY_LEVEL) or call.data.get("battery_level")
        
        _LOGGER.debug("Parsed parameters - vehicle_id: %s, home_id: %s, battery_level: %s", 
                     vehicle_id, home_id, battery_level)
        
        # Validate required parameters
        if not vehicle_id:
            _LOGGER.error("Missing required parameter: vehicle_id")
            return
        if not home_id:
            _LOGGER.error("Missing required parameter: home_id")
            return
        if battery_level is None:
            _LOGGER.error("Missing required parameter: battery_level")
            return

        try:
            # Validate battery level is within valid range
            if not isinstance(battery_level, (int, float)) or battery_level < 0 or battery_level > 100:
                _LOGGER.error("Invalid battery level: %s. Must be between 0 and 100", battery_level)
                return
                
            await api.execute_gql(
                MUTATION_SET_VEHICLE_SOC,
                {
                    "vehicleId": vehicle_id,
                    "homeId": home_id,
                    "settings": [
                        {
                            "key": "offline.vehicle.batteryLevel",
                            "value": int(battery_level)  # Ensure it's an integer
                        }
                    ]
                }
            )
            _LOGGER.info(
                "Successfully set vehicle %s SoC to %s%%",
                vehicle_id,
                battery_level
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set vehicle %s SoC: %s",
                vehicle_id,
                err
            )

    async def get_connected_vehicle(call: ServiceCall) -> None:
        """Get the currently connected vehicle."""
        _LOGGER.debug("Get connected vehicle service called with data: %s", call.data)
        
        home_id = call.data.get(ATTR_HOME_ID) or call.data.get("home_id")
        
        if not home_id:
            _LOGGER.error("Missing required parameter: home_id")
            return
        
        try:
            # Import the query
            from .const import QUERY_GET_CONNECTED_VEHICLE
            
            # Execute GraphQL query
            result = await api.execute_gql(QUERY_GET_CONNECTED_VEHICLE, {"homeId": home_id})
            
            if "data" in result and "me" in result["data"] and "home" in result["data"]["me"]:
                home_data = result["data"]["me"]["home"]
                vehicles = home_data.get("vehicles", [])
                
                # Find connected vehicles
                connected_vehicles = [
                    {
                        "id": vehicle["id"],
                        "name": vehicle.get("name", "Unknown"),
                        "model": vehicle.get("model", "Unknown"),
                        "battery_level": vehicle.get("batteryLevel"),
                        "range": vehicle.get("range"),
                        "connected": vehicle.get("connected", False),
                        "charging": vehicle.get("charging", False),
                        "charging_power": vehicle.get("chargingPower")
                    }
                    for vehicle in vehicles
                    if vehicle.get("connected", False)
                ]
                
                if connected_vehicles:
                    _LOGGER.info("Found %d connected vehicle(s): %s", 
                               len(connected_vehicles), 
                               [v["name"] for v in connected_vehicles])
                    
                    # Return the first connected vehicle (most common case)
                    return connected_vehicles[0]
                else:
                    _LOGGER.info("No vehicles currently connected")
                    return None
            else:
                _LOGGER.error("Invalid response structure from Tibber API")
                return None
                
        except Exception as err:
            _LOGGER.error("Failed to get connected vehicle: %s", err)
            return None

    hass.services.async_register(DOMAIN, "set_vehicle_soc", set_vehicle_soc)
    hass.services.async_register(DOMAIN, "get_connected_vehicle", get_connected_vehicle)

    # Set up periodic token refresh (every 18 hours)
    async def refresh_token():
        """Periodically refresh the authentication token."""
        try:
            await api.authenticate()
            _LOGGER.debug("Token refreshed successfully")
        except Exception as err:
            _LOGGER.error("Failed to refresh token: %s", err)

    # Schedule token refresh every 18 hours (64800 seconds)
    async_track_time_interval(
        hass,
        refresh_token, 
        timedelta(hours=18)
    )

    # No platforms to setup for service-only integration
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Service-only integration, just clean up data
    hass.data[DOMAIN].pop(entry.entry_id)
    return True

class TibberGraphAPI:
    """Handle all communication with the Tibber GraphAPI."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._username = username
        self._password = password
        self._token = None
        self._token_expires_at = None
        self._headers = {
            "Accept-Language": "en",
            "x-tibber-new-ui": "true",
            "User-Agent": "Tibber/25.20.0 (versionCode: 2520004Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011))",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://app.tibber.com",
            "Referer": "https://app.tibber.com/",
        }
        self._endpoint = "https://app.tibber.com/v4/gql"
        self._login_url = "https://app.tibber.com/login.credentials"
        
        # Alternative endpoints to try if primary ones fail
        # Note: Only use the primary GraphQL endpoint as per reverse engineering
        self._alternative_endpoints = [
            "https://app.tibber.com/v4/gql",  # Primary endpoint only
        ]
        self._alternative_login_urls = [
            "https://app.tibber.com/login.credentials",
            "https://api.tibber.com/v1-beta/login",
            "https://api.tibber.com/v1/login",
            "https://app.tibber.com/login",
            "https://api.tibber.com/login",
        ]

    async def _test_endpoint(self, url: str) -> bool:
        """Test if an endpoint is accessible."""
        try:
            async with async_timeout.timeout(5):
                # For GraphQL endpoints, try a simple POST request
                if "gql" in url:
                    response = await self._session.post(
                        url, 
                        json={"query": "query { __typename }"},
                        headers={**self._headers, "Content-Type": "application/json"}
                    )
                    # Accept 200 (success) or 401 (auth required) as valid responses
                    return response.status in [200, 401]
                else:
                    # For login endpoints, try GET
                    response = await self._session.get(url, headers=self._headers)
                    return response.status in [200, 404, 405]  # 404/405 are OK, means endpoint exists but method wrong
        except Exception:
            return False

    async def _find_working_endpoints(self) -> tuple[str, str]:
        """Find working login and GraphQL endpoints."""
        _LOGGER.debug("Testing endpoint accessibility...")
        
        # Always use the primary endpoints (most reliable)
        login_url = self._login_url
        endpoint = self._endpoint
        
        _LOGGER.debug("Using primary login endpoint: %s", login_url)
        _LOGGER.debug("Using primary GraphQL endpoint: %s", endpoint)
        
        # Test if the primary GraphQL endpoint is accessible
        if await self._test_endpoint(endpoint):
            _LOGGER.debug("Primary GraphQL endpoint is accessible")
        else:
            _LOGGER.debug("Primary GraphQL endpoint test failed, but will use it anyway")
            
        return login_url, endpoint

    def _update_headers_for_gql(self, token: str) -> None:
        """Update headers for GraphQL requests."""
        self._headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "application/graphql-response+json, application/json",
            "x-tibber-new-ui": "true",
        })

    def _validate_token_scopes(self, token: str) -> bool:
        """Validate that the JWT token has the required scopes."""
        try:
            import base64
            import json
            
            # JWT tokens have 3 parts separated by dots
            parts = token.split('.')
            if len(parts) != 3:
                _LOGGER.warning("Invalid JWT token format")
                return False
                
            # Decode the payload (second part)
            payload = parts[1]
            # Add padding if needed
            payload += '=' * (4 - len(payload) % 4)
            decoded = base64.b64decode(payload)
            data = json.loads(decoded)
            
            # Check for required scopes
            scopes = data.get('scopes', [])
            required_scopes = ['gw-api-write', 'gw-api-read', 'gw-web']
            
            if not all(scope in scopes for scope in required_scopes):
                _LOGGER.warning("Token missing required scopes. Required: %s, Found: %s", 
                              required_scopes, scopes)
                return False
                
            _LOGGER.debug("Token scopes validated successfully: %s", scopes)
            return True
            
        except Exception as e:
            _LOGGER.warning("Failed to validate token scopes: %s", e)
            return True  # Don't fail if we can't validate

    async def _try_authentication_methods(self, login_url: str) -> dict:
        """Try different authentication methods for a given URL."""
        methods = [
            # Method 1: Form data (original)
            {
                "data": f"email={self._username}&password={self._password}",
                "headers": self._headers.copy()
            },
            # Method 2: JSON payload
            {
                "json": {"email": self._username, "password": self._password},
                "headers": {**self._headers, "Content-Type": "application/json"}
            },
            # Method 3: Different form format
            {
                "data": {"email": self._username, "password": self._password},
                "headers": self._headers.copy()
            }
        ]
        
        for i, method in enumerate(methods, 1):
            _LOGGER.debug("Trying authentication method %d for %s", i, login_url)
            try:
                async with async_timeout.timeout(15):
                    response = await self._session.post(
                        login_url,
                        **method
                    )
                    
                    _LOGGER.debug("Method %d response status: %s", i, response.status)
                    
                    if response.status == 200:
                        try:
                            data = await response.json()
                            if "token" in data:
                                _LOGGER.info("Authentication method %d successful!", i)
                                return data
                        except Exception as json_err:
                            _LOGGER.debug("Method %d failed to parse JSON: %s", i, json_err)
                    else:
                        response_text = await response.text()
                        _LOGGER.debug("Method %d failed with status %s: %s", i, response.status, response_text[:200])
                        
            except Exception as e:
                _LOGGER.debug("Method %d failed with exception: %s", i, e)
                
        return None

    async def _retry_with_delay(self, func, max_retries: int = 3, delay: float = 2.0):
        """Retry a function with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return await func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                
                wait_time = delay * (2 ** attempt)  # Exponential backoff
                _LOGGER.debug("Attempt %d failed, retrying in %.1f seconds: %s", 
                            attempt + 1, wait_time, e)
                await asyncio.sleep(wait_time)

    async def authenticate(self) -> None:
        """Authenticate with Tibber and get JWT token."""
        async def _auth_attempt():
            # Try to find working endpoints first
            login_url, endpoint = await self._find_working_endpoints()
            self._login_url = login_url
            self._endpoint = endpoint
            
            _LOGGER.debug("Attempting to authenticate with Tibber at %s", self._login_url)
            _LOGGER.debug("Using headers: %s", {k: v for k, v in self._headers.items() if k != "Authorization"})
            
            # Try different authentication methods
            data = await self._try_authentication_methods(self._login_url)
            
            if data:
                _LOGGER.debug("Authentication response data keys: %s", list(data.keys()) if isinstance(data, dict) else "Not a dict")
                
                if "token" not in data:
                    _LOGGER.error("No token in authentication response: %s", data)
                    raise Exception("No token received from authentication")
                
                # Validate token scopes
                if not self._validate_token_scopes(data['token']):
                    _LOGGER.warning("Token scopes validation failed, but continuing...")
                
                # Update headers for subsequent GraphQL requests
                self._update_headers_for_gql(data['token'])
                self._token = data["token"]
                
                # Ensure we're using the correct GraphQL endpoint
                self._endpoint = "https://app.tibber.com/v4/gql"
                
                # Set token expiry (JWT tokens typically expire in 20 hours)
                # We'll refresh 1 hour before expiry to be safe
                import time
                self._token_expires_at = time.time() + (18 * 3600)  # 18 hours from now
                
                _LOGGER.info("Successfully authenticated with Tibber, token expires at: %s", 
                           self._token_expires_at)
                _LOGGER.info("Using GraphQL endpoint: %s", self._endpoint)
                return
            
            # If primary endpoint failed, try alternative endpoints
            _LOGGER.warning("Primary authentication failed, trying alternative endpoints...")
            
            for alt_login_url in self._alternative_login_urls:
                if alt_login_url != self._login_url:
                    _LOGGER.info("Trying alternative login endpoint: %s", alt_login_url)
                    data = await self._try_authentication_methods(alt_login_url)
                    
                    if data and "token" in data:
                        _LOGGER.info("Successfully authenticated with alternative endpoint: %s", alt_login_url)
                        self._login_url = alt_login_url
                        
                        # Validate token scopes
                        if not self._validate_token_scopes(data['token']):
                            _LOGGER.warning("Alternative endpoint token scopes validation failed, but continuing...")
                        
                        # Update headers for subsequent GraphQL requests
                        self._update_headers_for_gql(data['token'])
                        self._token = data["token"]
                        
                        # Ensure we're using the correct GraphQL endpoint
                        self._endpoint = "https://app.tibber.com/v4/gql"
                        
                        # Set token expiry
                        import time
                        self._token_expires_at = time.time() + (18 * 3600)
                        _LOGGER.info("Using GraphQL endpoint: %s", self._endpoint)
                        return
            
            # If all endpoints failed
            raise Exception("Authentication failed: All endpoints returned HTML error pages or failed")
        
        # Use retry logic with exponential backoff
        await self._retry_with_delay(_auth_attempt, max_retries=3, delay=5.0)

    async def execute_gql(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query."""
        import time
        
        # Check if token needs refresh (1 hour before expiry)
        if not self._token or (self._token_expires_at and time.time() >= self._token_expires_at):
            _LOGGER.debug("Token expired or missing, refreshing authentication")
            await self.authenticate()

        # Ensure we're using the correct endpoint
        if not self._endpoint or not self._endpoint.startswith("https://app.tibber.com/v4/gql"):
            _LOGGER.warning("Using non-standard GraphQL endpoint: %s", self._endpoint)
            _LOGGER.info("Forcing use of primary endpoint: https://app.tibber.com/v4/gql")
            self._endpoint = "https://app.tibber.com/v4/gql"

        _LOGGER.debug("Executing GraphQL query to %s", self._endpoint)
        _LOGGER.debug("Query: %s", query[:200] + "..." if len(query) > 200 else query)
        _LOGGER.debug("Variables: %s", variables)

        try:
            async with async_timeout.timeout(15):
                response = await self._session.post(
                    self._endpoint,
                    json={"query": query, "variables": variables or {}},
                    headers=self._headers,
                )
                
                _LOGGER.debug("GraphQL response status: %s", response.status)
                _LOGGER.debug("Response headers: %s", dict(response.headers))
                
                if response.status == 401:
                    # Token expired, re-authenticate and retry once
                    _LOGGER.debug("Received 401, refreshing token and retrying")
                    await self.authenticate()
                    response = await self._session.post(
                        self._endpoint,
                        json={"query": query, "variables": variables or {}},
                        headers=self._headers,
                    )
                    _LOGGER.debug("Retry response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("GraphQL query failed with status %s", response.status)
                    _LOGGER.error("Response text: %s", response_text[:500])  # Limit log size
                    
                    # Check if it's an HTML error page
                    if "<!DOCTYPE html>" in response_text or "<html" in response_text:
                        raise Exception(f"Query failed: {response.status} - Received HTML error page (possibly endpoint changed or blocked)")
                    else:
                        raise Exception(f"Query failed: {response.status} - {response_text}")
                
                try:
                    data = await response.json()
                    _LOGGER.debug("GraphQL response data keys: %s", list(data.keys()) if isinstance(data, dict) else "Not a dict")
                    
                    if "errors" in data:
                        _LOGGER.error("GraphQL errors: %s", data["errors"])
                        raise Exception(f"Query failed: {data['errors']}")
                    
                    if "data" not in data:
                        _LOGGER.error("No data in GraphQL response: %s", data)
                        raise Exception("No data in GraphQL response")
                    
                    return data["data"]
                except Exception as json_err:
                    response_text = await response.text()
                    _LOGGER.error("Failed to parse GraphQL response as JSON: %s", json_err)
                    _LOGGER.error("Response text: %s", response_text[:500])  # Limit log size
                    raise Exception(f"Invalid JSON response: {json_err}")
                    
        except asyncio.TimeoutError as err:
            _LOGGER.error("GraphQL query timed out after 15 seconds")
            raise Exception("GraphQL query timed out") from err
        except Exception as err:
            _LOGGER.exception("Failed to execute GraphQL query")
            raise 