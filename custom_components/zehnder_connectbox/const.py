"""Constants for the Zehnder ConnectBox integration."""

from datetime import timedelta

DOMAIN = "zehnder_connectbox"

CONF_APP_ID = "app_id"
CONF_APP_UUID = "app_uuid"
CONF_CERTIFICATE_SHA256 = "certificate_sha256"
CONF_GATEWAY_UUID = "gateway_uuid"
CONF_REMOTE_UUID = "remote_uuid"

DEFAULT_NAME = "Zehnder ConnectBox"
DEFAULT_PORT = 36643
DISCOVERY_TIMEOUT = 3.0
PAIRING_TIMEOUT = 120.0
POLL_INTERVAL = timedelta(seconds=10)
PROPERTY_REFRESH_INTERVAL = 60.0
FILTER_PROPERTY_REFRESH_INTERVAL = 15 * 60.0

SUPPORTED_PRODUCT_TYPE = 30
PRODUCT_VARIANT_COMFOSPOT_50 = 1
PRODUCT_VARIANT_COMFOAIR_70 = 2
SUPPORTED_VARIANTS = {
    PRODUCT_VARIANT_COMFOSPOT_50,
    PRODUCT_VARIANT_COMFOAIR_70,
}

# Room ventilation values 0-4 are verified fan levels. The unit's control panel
# places sensor-controlled operation ("Lüfterstufe AUTO") directly after level
# 4, and the official app offers it per room and situation in place of a fan
# level. Six ComfoSpot 50 units set to Auto in the app reported this value for
# the active situation; writing it still needs one confirmed test.
VENTILATION_LEVELS = (0, 1, 2, 3, 4)
SENSOR_MODE_LEVEL = 5
