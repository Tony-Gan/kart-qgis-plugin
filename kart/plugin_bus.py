from qgis.PyQt.QtCore import QObject, pyqtSignal, QVariant
from qgis.core import QgsApplication
import uuid, sys

BUS_PROPERTY_KEY = "_qgis_plugin_request/response_bus_v1"

class RequestResponseBus(QObject):
    """
    Synchronous inter-plugin request/response bus **without timeout**
    """

    request = pyqtSignal(str, object)   # request_id, payload
    response = pyqtSignal(str, object)  # request_id, result

    _instance = None
    __id = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            print(f"plugin_bus: new cls._instance")
        return cls._instance
    
    def __init__(self):
        super().__init__()
        if self.__id is None:
            self.__id = str(uuid.uuid4())
            print(f"plugin_bus: id: {self.__id} - initialized")

    def get_id(self):
        return self.__id

    def call(self, payload, caller=None):
        """
        Send a request and collect responses.

        :param payload: dict
        :param caller: any
        :return: list of responses
        """
        request_id = str(uuid.uuid4())
        responses = []

        def _on_response(rid, result):
            if rid == request_id:
                responses.append(result)

        # Connect temporary listener
        self.response.connect(_on_response)

        # Emit request (synchronous dispatch)
        self.request.emit(request_id, payload)

        # Cleanup (CRITICAL)
        self.response.disconnect(_on_response)

        return responses

def get_bus() -> RequestResponseBus:
    """
    Returns a singleton request/response bus shared across all plugins.
    """
    app = QgsApplication.instance()
    bus = app.property(BUS_PROPERTY_KEY)

    if bus is None:
        bus = RequestResponseBus()
        app.setProperty(BUS_PROPERTY_KEY, bus)

    return bus

def check_bus() -> int:
    """
    Returns the number of active object references using the shared bus (which may be higher than expected
    See: https://medium.com/@2019077_13406/garbage-collection-in-python-40dacb194cba)
    If the number of references indicates that there are no objects using the bus, then "garbage collection"
    is performed to drop the singleton QgsApplication bus instance (Property object) altogether.
    """
    refcount = 0

    # The way refcount works, any value >= 4 indicates that the singleton bus is in active use... somewhere
    # Similarly, a refcount < 4 indicates that the singleton bus exists, but is not being used by any objects. Therefore
    # it can be dereferenced and dropped altogether.
    #
    # QgsApplication property = 1       i.e. the singleton object with no plugin references yet created has a refcount of 1
    # First active plugin -->  +1 = 2   i.e. *at least* one new reference will be created for each plugin using the bus
    # testbus variable -->     +1 = 3   i.e. accessing the singleton bus for checking within this function adds another count
    # sys.getrefcount -->      +1 = 4   i.e. the refcount call itself adds another count


    testbus = QgsApplication.instance().property(BUS_PROPERTY_KEY)
    if testbus is not None:
        id = testbus.get_id()
        refcount = sys.getrefcount(testbus)

        if refcount < 4:
            # Drop the global singleton object altogether
            print(f"check_bus: id: {id} instance dropped. No active plugins (refcount {refcount})")
            QgsApplication.instance().setProperty(BUS_PROPERTY_KEY, QVariant())

        else:
            print(f"check_bus: id: {id} instance active. {refcount - 3} object references (refcount {refcount})")

    else:
        print(f"check_bus: No global message bus instance found")

    return refcount
