import quixlab as ql

canvas = ql.Canvas(title="My Notebook", lake_tree_open=['ac_telemetry_prod', 'ac_telemetry_prod/environment=prague_office', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd/experiment=tyre_pressure', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd/experiment=tyre_pressure/driver=tomas neubauer', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd/experiment=tyre_pressure/driver=tomas neubauer/track=Spa', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd/experiment=tyre_pressure/driver=tomas neubauer/track=Spa/carModel=porsche_991ii_gt3_r', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd/experiment=tyre_pressure/driver=tomas neubauer/track=Spa/carModel=porsche_991ii_gt3_r/session_id=2026-06-17T16:04:17.019Z'])


@canvas.dataset(position=(566, 140), size=(869, 539), code_height=200, viz={'type': 'table', 'x': '', 'y': ''})
def ac_telemetry_prod(data_selection):
    return ql.sql(f"""SELECT timestamp_ms, speedKmh, rpms, gear, gas, brake, lap
    FROM ac_telemetry_prod
    WHERE environment = 'prague_office'
      AND test_rig = 'fanatec_csl_dd'
      AND experiment = '{data_selection.experiment}'
      AND driver = '{data_selection.driver}'
    """)


@canvas.cell(position=(722, 1149), size=(815, 623), code_height=200, viz={'type': 'table', 'x': 'timestamp_ms', 'y': ['speedKmh']})
def cell_3(stream_1):
    import pandas as pd

    valid = stream_1.df[stream_1.df["rows"].notna()][["rows"]]

    if valid.empty:
        return pd.DataFrame()

    df = valid.iloc[[-1]].explode("rows").dropna(subset=["rows"])
    rows_df = pd.json_normalize(df["rows"])
    return rows_df     # peek at the first element


@canvas.cell(position=(-445, 97), size=(729, 526), code_height=200)
def data_selection():
    # Pin ancestor partition columns to skip the tree fan-out.
    experiments = ql.partition_values("ac_telemetry_prod", "experiment")
    experiment = ql.ui.dropdown(experiments, label="Experiment")

    drivers = ql.partition_values("ac_telemetry_prod", "driver", where={"experiment": experiment.value})
    driver = ql.ui.dropdown(drivers, label="Driver")

    experiment, driver


@canvas.cell(position=(1680, 74), size=(1259, 917), code_height=146, viz={'type': 'line', 'x': 'lap_time_ms', 'y': ['1', '2', '3']})
def cell_2(ac_telemetry_prod):
    df = ac_telemetry_prod
    df["lap_time_ms"] = df["timestamp_ms"] - df.groupby("lap")["timestamp_ms"].transform("min")
    wide = df.pivot_table(index="lap_time_ms", columns="lap", values="speedKmh", aggfunc="mean")
    wide = wide.interpolate(method="index", limit_direction="both")
    return wide.reset_index()


@canvas.stream(position=(-497, 1135), size=(834, 813), code_height=469)
def stream_1():
    return ql.topic("best-laps-events", workspace="quixdev-acquixbridge-leadboard", offset="earliest", limit=2000, consumer_group="quixlab-best-laps-events-f9zx33")


@canvas.dataset(position=(-560, -971), size=(849, 586), code_height=200)
def ac_telemetry_prod_2():
    return ql.sql("""SELECT lap, timestamp_ms, rpms, speedKmh
    FROM ac_telemetry_prod
    WHERE environment = 'prague_office'
      AND test_rig = 'fanatec_csl_dd'
      AND experiment = 'tyre_pressure'
      AND driver = 'tomas neubauer'
      AND track = 'Spa'
      AND carModel = 'porsche_991ii_gt3_r'
      AND session_id = '2026-06-17T16:04:17.019Z'
    ORDER BY timestamp_ms""")


@canvas.cell(position=(537, -1030), size=(1141, 692), code_height=200, viz={'appDeployment': {'id': '5a652cf6-6fab-4257-828b-794087e29ac3', 'kind': 'app', 'name': 'cell-1-app', 'portalUrl': 'https://portal.dev.quix.io/pipeline/deployments/5a652cf6-6fab-4257-828b-794087e29ac3?workspace=quixdev-acquixbridge-prod', 'publicUrl': ''}, 'appStatus': 'QueuedForBuild', 'type': 'line', 'x': 'timestamp_ms', 'y': 'rpms'})
def cell_1(ac_telemetry_prod_2):
    return ac_telemetry_prod_2


@canvas.notebook(position=(645, -1642), size=(734, 558), code_height=200, viz={'outputCell': 0})
def cell_4(ac_telemetry_prod_2):
    import plotly.graph_objects as go

    df = (
        ac_telemetry_prod_2
        .groupby("lap")["speedKmh"]
        .agg(min_speedKmh="min", max_speedKmh="max", mean_speedKmh="mean")
        .reset_index()
        .sort_values("lap")
    )

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["lap"], y=df["min_speedKmh"], mode="lines+markers", name="Min speed (km/h)"))
    fig.add_trace(go.Scatter(x=df["lap"], y=df["max_speedKmh"], mode="lines+markers", name="Max speed (km/h)"))
    fig.add_trace(go.Scatter(x=df["lap"], y=df["mean_speedKmh"], mode="lines+markers", name="Mean speed (km/h)"))
    fig.update_layout(title="Speed (km/h) per lap", xaxis_title="Lap", yaxis_title="Speed (km/h)")

    return fig


@canvas.cell(position=(1876, -1881), size=(838, 589), code_height=200, viz={'storagePath': 'quixdev-acquixbridge-prod', 'storageType': 'folder'})
def quixdev_acquixbridge_prod():
    ql.StorageFolder("quixdev-acquixbridge-prod")


@canvas.ai(position=(1911, -896), size=(991, 670), code_height=200, viz={'type': 'line', 'x': 'timestamp', 'y': ['rpms']})
def ai_1(cell_1):
    """Downsample this data to 1Hz using aggregation mean."""
    # ql-ai: generated from prompt 1ecc772fd952986a
    import pandas as pd

    df = cell_1.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp_ms'], unit='ms')
    df = df.set_index('timestamp').sort_index()

    downsampled = df.resample('1s').mean(numeric_only=True).dropna(how='all').reset_index()
    downsampled['timestamp_ms'] = downsampled['timestamp'].astype('int64') // 10**6
    downsampled['lap'] = downsampled['lap'].round().astype('Int64')

    downsampled[['timestamp', 'lap', 'timestamp_ms', 'rpms', 'speedKmh']]


if __name__ == "__main__":
    canvas.serve()
