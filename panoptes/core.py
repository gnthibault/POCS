import os
import signal
import sys
import warnings

from astropy.time import Time

# Append the POCS dir to the system path.
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from .utils.logger import has_logger
from .utils.config import load_config
from .utils.database import PanMongo

from .state_machine import PanStateMachine
from .weather import WeatherStationMongo, WeatherStationSimulator
from .observatory import Observatory


@has_logger
class Panoptes(PanStateMachine):

    """ A Panoptes object is in charge of the entire unit.

    An instance of this object is responsible for total control
    of a PANOPTES unit. Has access to the observatory, state machine,
    a parameter server, and a messaging channel.

    Args:
        connect_on_startup: Controls whether unit should try to connect
            when object is created. Defaults to False
    """

    def __init__(self, state_machine_file='simple_state_table', *args, **kwargs):
        # Setup utils for graceful shutdown
        signal.signal(signal.SIGINT, self._sigint_handler)

        # self.logger.info('*' * 80)
        self.logger.info('Initializing PANOPTES unit')
        self.logger.info('Using default state machine file: {}'.format(state_machine_file))

        state_machine_table = PanStateMachine.load_state_table(state_table_name=state_machine_file)

        # Initialize the state machine. See `PanStateMachine` for details.
        super().__init__(**state_machine_table)

        self._check_environment()

        self.logger.info('Checking config')
        self.config = load_config()
        self._check_config()

        # Setup the param server
        self.logger.info('Setting up database connection')
        self.db = PanMongo()

        self.weather_station = self._create_weather_station()

        # Create our observatory, which does the bulk of the work
        self.logger.info('Setting up observatory')
        self.observatory = Observatory(config=self.config)

##################################################################################################
# Methods
##################################################################################################

    def shutdown(self):
        """ Actions to be performed upon shutdown

        Note:
            This method is automatically called from the interrupt handler. The definition should
            include what you want to happen upon shutdown but you don't need to worry about calling
            it manually.
        """
        # Stop the INDI server
        self.server.stop()


##################################################################################################
# Conditions
##################################################################################################

    def weather_is_safe(self, event_data):
        """ Checks the safety flag of the weather

        Args:
            event_data(transitions.EventData): carries information about the event

        Returns:
            bool:   Latest safety flag of weather
        """
        is_safe = self.weather_station.is_safe()
        self.logger.info("Weather Safe: {}".format(is_safe))

        if not is_safe:
            self.logger.warning('Weather not safe')

        return is_safe

    def is_dark(self, event_data):
        """ Is it dark

        Args:
            event_data(transitions.EventData): carries information about the event

        Returns:
            bool:   Is night at location

        """
        is_dark = self.observatory.is_night(Time.now())
        self.logger.info("Is Night: {}".format(is_dark))
        return is_dark


##################################################################################################
# Private Methods
##################################################################################################

    def _check_environment(self):
        """ Checks to see if environment is set up correctly

        There are a number of environmental variables that are expected
        to be set in order for PANOPTES to work correctly. This method just
        sanity checks our environment.

            POCS    Base directory for PANOPTES
        """
        if os.getenv('POCS') is None:
            warnings.warn('Please make sure $POCS environment variable is set')
            self.shutdown()
            sys.exit(0)

    def _check_config(self):
        """ Checks the config file for mandatory items """
        if 'name' in self.config:
            self.logger.info('Welcome {}'.format(self.config.get('name')))

        if 'base_dir' not in self.config:
            raise error.InvalidConfig('base_dir must be specified in config_local.yaml')

        if 'mount' not in self.config:
            raise error.MountNotFound('Mount must be specified in config')

        if 'state_machine' not in self.config:
            raise error.InvalidConfig('State Table must be specified in config')

    def _create_weather_station(self):
        """ Determines which weather station to create base off of config values """
        weather_station = None

        # Lookup appropriate weather stations
        station_lookup = {
            'simulator': WeatherStationSimulator,
            'mongo': WeatherStationMongo,
        }
        weather_module = station_lookup.get(self.config['weather']['station'], WeatherStationMongo)

        self.logger.info('Setting up weather station {}'.format(weather_module))

        try:
            weather_station = weather_module()
        except:
            raise PanError(msg="Weather station could not be created")

        return weather_station

    def _sigint_handler(self, signum, frame):
        """
        Interrupt signal handler. Designed to intercept a Ctrl-C from
        the user and properly shut down the system.
        """
        self.logger.error("Signal handler called with signal ", signum)
        self.shutdown()
        sys.exit(0)

    def __del__(self):
        self.shutdown()
