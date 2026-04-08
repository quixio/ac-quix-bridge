from influxdb import InfluxDBClient

from .models import LogbookEntry
from .settings import InfluxSettings


class _Logbook:
    def __init__(self, client: InfluxDBClient, measurement: str):
        self._client = client
        self._measurement = measurement

    def write(self, entry: LogbookEntry) -> None:
        point = {
            "measurement": self._measurement,
            "tags": {
                "id": entry.id,
            },
            "fields": {
                "test_id": entry.test_id,
                "content": entry.content,
                "operator": entry.operator,
                "sensor_ids": ",".join(entry.sensor_ids),
                "created_at": int(entry.created_at.timestamp()),
            },
            "time": int(entry.timestamp.timestamp()),
        }
        self._client.write_points([point], time_precision="s")

    def delete(self, entry_id: str) -> None:
        self._client.delete_series(measurement=self._measurement, tags={"id": entry_id})


class Influx:
    """Small wrapper around the InfluxDBClient to make it easier to use."""

    def __init__(self, client: InfluxDBClient, measurement: str):
        self.logbook = _Logbook(client=client, measurement=measurement)


_influx: Influx


def connect(settings: InfluxSettings) -> None:
    global _influx
    client = InfluxDBClient(
        host=settings.host,
        port=settings.port,
        username=settings.user,
        password=settings.password,
        database=settings.database,
    )
    _influx = Influx(client=client, measurement=settings.measurement)
    if settings.database not in {db["name"] for db in client.get_list_database()}:
        client.create_database(settings.database)


def get_influx() -> Influx:
    return _influx
