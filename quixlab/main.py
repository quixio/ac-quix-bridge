import quixlab as ql

canvas = ql.Canvas(title="My Notebook", lake_tree_open=['ac_telemetry_prod', 'ac_telemetry_prod/environment=prague_office', 'ac_telemetry_prod/environment=prague_office/test_rig=fanatec_csl_dd'])


@canvas.dataset(position=(420, 77), size=(839, 477), code_height=200)
def ac_telemetry_prod():
    return ql.sql("""SELECT *
    FROM ac_telemetry_prod
    WHERE environment = 'prague_office'
      AND test_rig = 'fanatec_csl_dd'
      AND experiment = 'tyre_pressure'
      AND driver = 'tomas neubauer'
    """)


@canvas.dataset(position=(-2185, -641), size=(844, 613), code_height=230)
def car_telemetry():
    return ql.sql("""SELECT date, ts_ms, speed, rpm, n_gear
    FROM car_telemetry
    WHERE year = 2023
      AND circuit = 'Monza'
      AND session_type = 'Race'
      AND session_name = 'Race'
      AND driver_acronym = 'HAM'
      AND lap_number = 12
    ORDER BY ts_ms
    """)


@canvas.cell(position=(-1179, -641), size=(937, 630), code_height=200, viz={'type': 'line', 'x': 'ts_ms', 'y': ['speed']})
def cell_1(car_telemetry):
    return [
        ql.ui.markdown(r"""
    # Channel explorer

    Some **markdown** text.
    """),
        car_telemetry]


@canvas.stream(position=(337, 709), size=(559, 870), code_height=496, viz={'refresh': 10, 'type': 'table', 'x': 'packetId', 'y': 'gas'})
def stream_1():
    return ql.topic("ac-telemetry-raw", workspace="quixdev-acquixbridge-prod", offset="earliest", limit=200000, consumer_group="quixlab-ac-telemetry-raw-ipz94c")


@canvas.cell(position=(1014, 1344), size=(815, 623), code_height=200, viz={'type': 'waveform', 'x': 'timestamp_ms', 'y': ['speedKmh']})
def cell_3(stream_1):
    df = stream_1.df[["timestamp_ms", "completedLaps", "speedKmh", "rpms", "gear"]]
    return df.tail(2000)


@canvas.notebook(position=(957, 709), size=(660, 546), code_height=200)
def cell_2(stream_1):
    # %%
    df = stream_1.df[["completedLaps", "speedKmh", "rpms", "gear"]]
    df["lap"] = df["completedLaps"] + 1


    stats_df = df.groupby("lap").agg(["min", "max"])
    return stats_df.reset_index()


if __name__ == "__main__":
    canvas.serve()
