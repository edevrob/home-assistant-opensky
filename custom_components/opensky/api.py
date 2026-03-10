"""Local OpenSky API client using OAuth2 client credentials."""

from __future__ import annotations

import asyncio
import math
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession
from aiohttp.hdrs import METH_GET, METH_POST
from yarl import URL

TOKEN_URL = URL(
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)
API_URL = URL("https://opensky-network.org/api/")


class OpenSkyApiError(Exception):
    """Generic OpenSky API error."""


class OpenSkyApiConnectionError(OpenSkyApiError):
    """Raised for network-level request failures."""


class OpenSkyApiUnauthenticatedError(OpenSkyApiError):
    """Raised when authentication with OpenSky fails."""


@dataclass(slots=True)
class BoundingBox:
    """Bounding box for retrieving state vectors."""

    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float


@dataclass(slots=True)
class StateVector:
    """Subset of OpenSky state vector fields used by the integration."""

    icao24: str
    callsign: str | None
    origin_country: str
    time_position: int | None
    last_contact: int
    longitude: float | None
    latitude: float | None
    barometric_altitude: float | None
    on_ground: bool
    velocity: float | None
    true_track: float | None
    vertical_rate: float | None
    sensors: list[int] | None
    geo_altitude: float | None
    transponder_code: str | None
    special_purpose_indicator: bool
    position_source: int | None
    category: int | None

    @classmethod
    def from_api(cls, data: list[Any]) -> StateVector:
        """Convert OpenSky array response into a typed state vector."""
        return cls(
            icao24=data[0],
            callsign=data[1],
            origin_country=data[2],
            time_position=data[3],
            last_contact=data[4],
            longitude=data[5],
            latitude=data[6],
            barometric_altitude=data[7],
            on_ground=data[8],
            velocity=data[9],
            true_track=data[10],
            vertical_rate=data[11],
            sensors=data[12],
            geo_altitude=data[13],
            transponder_code=data[14],
            special_purpose_indicator=data[15],
            position_source=data[16],
            category=data[17],
        )


@dataclass(slots=True)
class StatesResponse:
    """Represents the states response."""

    states: list[StateVector]
    time: int

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StatesResponse:
        """Initialize from API JSON."""
        states = data.get("states") or []
        return cls(
            time=data["time"],
            states=[StateVector.from_api(vector) for vector in states],
        )


@dataclass
class OpenSkyApiClient:
    """Minimal OpenSky client for this integration."""

    session: ClientSession
    request_timeout: int = 10
    client_id: str | None = None
    client_secret: str | None = None
    opensky_credits: int = 400
    _access_token: str | None = None
    _token_expires_at: datetime | None = None
    _contributing_user: bool = False
    _credit_usage: dict[datetime, int] = field(default_factory=dict)

    async def authenticate(
        self,
        client_id: str,
        client_secret: str,
        *,
        contributing_user: bool = False,
    ) -> None:
        """Validate OAuth credentials and configure credit limits."""
        self.client_id = client_id
        self.client_secret = client_secret
        await self._refresh_access_token()
        self._contributing_user = contributing_user
        self.opensky_credits = 8000 if contributing_user else 4000

    @property
    def is_authenticated(self) -> bool:
        """Return whether OAuth credentials are configured."""
        return bool(self.client_id and self.client_secret)

    async def get_states(
        self,
        bounding_box: BoundingBox | None = None,
    ) -> StatesResponse:
        """Retrieve state vectors for a given bounding box."""
        credit_cost = 4
        params: dict[str, Any] = {"time": 0, "extended": "true"}

        if bounding_box:
            params["lamin"] = bounding_box.min_latitude
            params["lamax"] = bounding_box.max_latitude
            params["lomin"] = bounding_box.min_longitude
            params["lomax"] = bounding_box.max_longitude
            credit_cost = self.calculate_credit_costs(bounding_box)

        data = await self._request("states/all", params=params)
        self._register_credit_usage(credit_cost)
        return StatesResponse.from_api(data)

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated OpenSky API request."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "HomeAssistantOpenSky/1.0",
        }

        if self.is_authenticated:
            await self._ensure_access_token()
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            async with asyncio.timeout(self.request_timeout):
                response = await self.session.request(
                    METH_GET,
                    API_URL.joinpath(path),
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
        except asyncio.TimeoutError as exception:
            raise OpenSkyApiConnectionError(
                "Timeout occurred while connecting to the OpenSky API"
            ) from exception
        except (
            ClientError,
            ClientResponseError,
            socket.gaierror,
        ) as exception:
            if isinstance(exception, ClientResponseError) and exception.status in {
                401,
                403,
            }:
                raise OpenSkyApiUnauthenticatedError from exception
            raise OpenSkyApiConnectionError(
                "Error occurred while communicating with OpenSky API"
            ) from exception

        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise OpenSkyApiError(
                f"Unexpected response from the OpenSky API: {content_type}"
            )
        return await response.json()

    async def _ensure_access_token(self) -> None:
        """Refresh the token shortly before it expires."""
        if self._access_token is None or self._token_expiring_soon():
            await self._refresh_access_token()

    def _token_expiring_soon(self) -> bool:
        """Return whether the token should be refreshed."""
        if self._token_expires_at is None:
            return True
        return datetime.now(UTC) + timedelta(seconds=60) >= self._token_expires_at

    async def _refresh_access_token(self) -> None:
        """Exchange client credentials for an access token."""
        if not self.client_id or not self.client_secret:
            raise OpenSkyApiUnauthenticatedError

        try:
            async with asyncio.timeout(self.request_timeout):
                response = await self.session.request(
                    METH_POST,
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
        except asyncio.TimeoutError as exception:
            raise OpenSkyApiConnectionError(
                "Timeout occurred while connecting to the OpenSky auth endpoint"
            ) from exception
        except (
            ClientError,
            ClientResponseError,
            socket.gaierror,
        ) as exception:
            if isinstance(exception, ClientResponseError) and exception.status in {
                400,
                401,
                403,
            }:
                self._access_token = None
                self._token_expires_at = None
                raise OpenSkyApiUnauthenticatedError from exception
            raise OpenSkyApiConnectionError(
                "Error occurred while communicating with OpenSky auth endpoint"
            ) from exception

        payload = await response.json()
        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 1800))
        self._token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    @staticmethod
    def calculate_credit_costs(bounding_box: BoundingBox) -> int:
        """Calculate the amount of credits a request costs."""
        latitude_degrees = bounding_box.max_latitude - bounding_box.min_latitude
        longitude_degrees = bounding_box.max_longitude - bounding_box.min_longitude
        area = latitude_degrees * longitude_degrees
        if area < 25:
            return 1
        if area < 100:
            return 2
        if area < 400:
            return 3
        return 4

    def _register_credit_usage(self, opensky_credits: int) -> None:
        """Track request cost for the last 24 hours."""
        self._credit_usage[datetime.now(UTC)] = opensky_credits

    @staticmethod
    def get_bounding_box(
        latitude: float,
        longitude: float,
        radius: float,
    ) -> BoundingBox:
        """Get bounding box from radius and a point."""
        half_side_in_km = abs(radius) / 1000

        lat = math.radians(latitude)
        lon = math.radians(longitude)

        approx_earth_radius = 6371
        hypotenuse_distance = math.sqrt(2 * (math.pow(half_side_in_km, 2)))

        lat_min = math.asin(
            math.sin(lat) * math.cos(hypotenuse_distance / approx_earth_radius)
            + math.cos(lat)
            * math.sin(hypotenuse_distance / approx_earth_radius)
            * math.cos(225 * (math.pi / 180)),
        )
        lon_min = lon + math.atan2(
            math.sin(225 * (math.pi / 180))
            * math.sin(hypotenuse_distance / approx_earth_radius)
            * math.cos(lat),
            math.cos(hypotenuse_distance / approx_earth_radius)
            - math.sin(lat) * math.sin(lat_min),
        )

        lat_max = math.asin(
            math.sin(lat) * math.cos(hypotenuse_distance / approx_earth_radius)
            + math.cos(lat)
            * math.sin(hypotenuse_distance / approx_earth_radius)
            * math.cos(45 * (math.pi / 180)),
        )
        lon_max = lon + math.atan2(
            math.sin(45 * (math.pi / 180))
            * math.sin(hypotenuse_distance / approx_earth_radius)
            * math.cos(lat),
            math.cos(hypotenuse_distance / approx_earth_radius)
            - math.sin(lat) * math.sin(lat_max),
        )

        return BoundingBox(
            min_latitude=math.degrees(lat_min),
            max_latitude=math.degrees(lat_max),
            min_longitude=math.degrees(lon_min),
            max_longitude=math.degrees(lon_max),
        )
