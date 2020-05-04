import json
import logging
import re

from wb_hass_gw.base_connector import BaseConnector
from wb_hass_gw.mappers import apply_payload_for_component
from wb_hass_gw.wirenboard_registry import WirenControl, WirenDevice, WirenBoardDeviceRegistry

logger = logging.getLogger(__name__)


class HomeAssistantConnector(BaseConnector):
    wiren = None

    def __init__(self, broker_host, broker_port, username, password, client_id,
                 topic_prefix,
                 entity_prefix,
                 discovery_topic,
                 status_topic,
                 status_payload_online,
                 status_payload_offline,
                 ):
        super().__init__(broker_host, broker_port, username, password, client_id)

        self._topic_prefix = topic_prefix
        self._entity_prefix = entity_prefix
        self._discovery_prefix = discovery_topic
        self._status_topic = status_topic
        self._status_payload_online = status_payload_online
        self._status_payload_offline = status_payload_offline

        self._control_set_topic_re = re.compile(self._topic_prefix + r"devices/([^/]*)/controls/([^/]*)/on$")

    def _subscribe(self):
        self._client.subscribe(self._status_topic)
        self._client.subscribe(f"{self._topic_prefix}devices/+/controls/+/on")

    async def _on_message(self, client, topic, payload, qos, properties):
        # print(f'RECV MSG: {topic}', payload)
        payload = payload.decode("utf-8")
        if topic == self._status_topic:
            if payload == self._status_payload_online:
                logger.info('Home assistant changed status to online. Pushing all devices')
                for device in WirenBoardDeviceRegistry().devices.values():
                    for control in device.controls.values():
                        self.publish_control(device, control)
            elif payload == self._status_payload_offline:
                logger.info('Home assistant changed status to offline')
            else:
                logger.error(f'Invalid payload for status topic ({topic} -> {payload})')
        else:
            control_set_state_topic_match = self._control_set_topic_re.match(topic)
            if control_set_state_topic_match:
                device = WirenBoardDeviceRegistry().get_device(control_set_state_topic_match.group(1))
                control = device.get_control(control_set_state_topic_match.group(2))
                self.wiren.set_control_state(device, control, payload, retain=properties['retain'])

    def set_control_state(self, device, control, payload, retain):
        target_topic = f"{self._topic_prefix}devices/{device.id}/controls/{control.id}"
        self._client.publish(target_topic, payload, qos=0, retain=retain)

    def _get_control_topic(self, device: WirenDevice, control: WirenControl):
        return f"{self._topic_prefix}devices/{device.id}/controls/{control.id}"

    def _get_availability_topic(self, device: WirenDevice, control: WirenControl):
        return f"{self._get_control_topic(device, control)}/availability"

    def publish_availability(self, device: WirenDevice, control: WirenControl):
        if not control.error:
            self._client.publish(self._get_availability_topic(device, control), '1', qos=1, retain=1)
        else:
            self._client.publish(self._get_availability_topic(device, control), '0', qos=1, retain=1)

    def publish_control(self, device: WirenDevice, control: WirenControl):
        """
        Publish discovery topic to the HA
        """

        if self._entity_prefix:
            entity_id_prefix = self._entity_prefix.lower().replace(" ", "_").replace("-", "_") + '_'
        else:
            entity_id_prefix = ''

        if WirenBoardDeviceRegistry().is_local_device(device):
            device_unique_id = entity_id_prefix + 'wirenboard'
            device_name = self._entity_prefix + ' Wirenboard'
        else:
            device_unique_id = entity_id_prefix + device.id
            device_name = self._entity_prefix + ' ' + device.name
        device_unique_id = device_unique_id.lower().replace(" ", "_").replace("-", "_")

        entity_unique_id = f"{entity_id_prefix}{device.id}_{control.id}".lower().replace(" ", "_").replace("-", "_")
        entity_name = f"{self._entity_prefix} {device.id} {control.id}".replace("_", " ").title()

        # common payload
        payload = {
            'device': {
                'name': device_name,
                'identifiers': device_unique_id
            },
            'name': entity_name,
            'unique_id': entity_unique_id,
            'availability_topic': self._get_availability_topic(device, control),
            'payload_available': "1",
            'payload_not_available': "0"
        }

        control_topic = self._get_control_topic(device, control)
        component = apply_payload_for_component(payload, device, control, control_topic)

        if not component:
            logger.warning(f'{device}: Unknown type of wirenboard control: {control}')
            return

        # Topic path: <discovery_topic>/<component>/[<node_id>/]<object_id>/config
        topic = self._discovery_prefix + '/' + component + '/' + entity_unique_id + '/config'
        logger.info(f'[{device.id}] {topic} ({control})')
        self._client.publish(topic, json.dumps(payload), qos=1)
        self.publish_availability(device, control)


