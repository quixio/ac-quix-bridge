from quixstreams import Application
from ac_source import AssettoCorsaSource
import os

app = Application()
source = AssettoCorsaSource(name="ac-telemetry-source")
sdf = app.dataframe(source=source)
sdf = sdf.print()

if __name__ == "__main__":
    app.run()
