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


@canvas.stream(position=(337, 709), size=(563, 512), code_height=232)
def stream_1():
    return ql.topic('', limit=200000)


@canvas.cell(position=(957, 709), size=(660, 546), code_height=200)
def cell_2(stream_1):
    df = stream_1.df
    df["lap"] = df["completedLaps"] + 1

    stats_df = df.groupby("lap").agg(["min", "max"])
    return stats_df.reset_index()


if __name__ == "__main__":
    canvas.serve()
