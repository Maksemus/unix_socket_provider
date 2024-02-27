import os

from dotenv import load_dotenv
load_dotenv()

ENV = os.environ

MOONRAKER_HOST = ENV['MOONRAKER_HOST']
MOONRAKER_WEBSOCKET_PROVIDER_PORT = int(ENV['MOONRAKER_WEBSOCKET_PROVIDER_PORT'])
TEMP_UPDATE_TIME = 1.
