import quixlab as ql

canvas = ql.Canvas(title="My Notebook", lake_tree_open=['ac_telemetry_prod', 'ac_telemetry_prod/environment=prague_office', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd'])


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

    df = stream_1.df[stream_1.df["rows"].notna()][["rows"]].iloc[[-1]].explode("rows").dropna(subset=["rows"])
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
    wide = df.pivot(index="lap_time_ms", columns="lap", values="speedKmh")
    wide = wide.ffill()
    wide.reset_index()


@canvas.stream(position=(-497, 1135), size=(816, 670), code_height=469)
def stream_1():
    return ql.topic("best-laps-events", workspace="quixdev-acquixbridge-leadboard", offset="earliest", limit=2000, consumer_group="quixlab-best-laps-events-6p32bn")


if __name__ == "__main__":
    canvas.serve()
