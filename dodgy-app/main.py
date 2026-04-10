# import the Quix Streams modules for interacting with Kafka.
# For general info, see https://quix.io/docs/quix-streams/introduction.html
from quixstreams import Application

import os
import time
# for local dev, load env vars from a .env file
from dotenv import load_dotenv
load_dotenv()

print(os.environ["Quix__BlobStorage__Connection__Json"])
time.sleep(10000)