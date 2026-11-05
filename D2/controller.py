import networkx as nx
import os
import socket
from os_ken.lib import hub

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    MAIN_DISPATCHER,
    CONFIG_DISPATCHER,
    set_ev_cls
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.topology import event
from os_ken.topology.api import get_switch, get_link
from os_ken.lib.packet import (
    packet, ethernet, ether_types, ipv4, udp
)


class Part3Controller(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    TRAFFIC_QUEUES = {
        5003: 1,
        5004: 2
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.graph = nx.Graph()
        self.datapaths = {}
        self.hosts = {}
        self.switch_ports = {}
        self.force_alternate = False
        self.control_socket_path = "/tmp/sdn_controller_control.sock"
        hub.spawn(self.control_listener)

    def control_listener(self):
        try:
            os.unlink(self.control_socket_path)
        except FileNotFoundError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.control_socket_path)
        server.listen(5)

        self.logger.info("UDP loss-control socket listening")

        while True:
            connection, _ = server.accept()
            with connection:
                message = connection.recv(1024).decode().strip()

            if message.startswith("LOSS|") and not self.force_alternate:
                self.force_alternate = True

                for u, v, data in self.graph.edges(data=True):
                    data["weight"] = 100 if {u, v} == {1, 2} else 1

                self.logger.warning(
                    "UDP LOSS DETECTED: switching preferred route "
                    "away from link s1-s2"
                )

                self.clear_routes()
                self.install_known_routes()

                self.logger.warning(
                    "Alternate SDN route installed after UDP loss notification"
                )

    def flow(self, dp, priority, match, actions):
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        instructions = [
            parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=instructions
        )

        dp.send_msg(mod)

    def install_table_miss(self, dp):
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        self.flow(
            dp,
            0,
            parser.OFPMatch(),
            [
                parser.OFPActionOutput(
                    ofp.OFPP_CONTROLLER,
                    ofp.OFPCML_NO_BUFFER
                )
            ]
        )

    def classify_queue(self, pkt):
        ip_header = pkt.get_protocol(ipv4.ipv4)
        udp_header = pkt.get_protocol(udp.udp)

        if ip_header is None or udp_header is None:
            return None

        if udp_header.dst_port in self.TRAFFIC_QUEUES:
            return self.TRAFFIC_QUEUES[udp_header.dst_port]

        if udp_header.src_port in self.TRAFFIC_QUEUES:
            return self.TRAFFIC_QUEUES[udp_header.src_port]

        return None

    def install_traffic_flows(self, dp, dst_mac, out_port):
        parser = dp.ofproto_parser

        for port, queue_id in self.TRAFFIC_QUEUES.items():

            forward_match = parser.OFPMatch(
                eth_dst=dst_mac,
                eth_type=ether_types.ETH_TYPE_IP,
                ip_proto=17,
                udp_dst=port
            )

            reverse_match = parser.OFPMatch(
                eth_dst=dst_mac,
                eth_type=ether_types.ETH_TYPE_IP,
                ip_proto=17,
                udp_src=port
            )

            actions = [
                parser.OFPActionSetQueue(queue_id),
                parser.OFPActionOutput(out_port)
            ]

            self.flow(dp, 20, forward_match, actions)
            self.flow(dp, 20, reverse_match, actions)

            self.logger.info(
                'Traffic priority rule: port %s, queue %s, switch %s',
                port, queue_id, dp.id
            )

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.install_table_miss(dp)

        self.logger.info('Switch connected: %s', dp.id)

    @set_ev_cls([
        event.EventSwitchEnter,
        event.EventSwitchLeave,
        event.EventLinkAdd,
        event.EventLinkDelete
    ])
    def topology_changed(self, ev):
        self.refresh()

    def refresh(self):
        switches = get_switch(self, None)
        links = get_link(self, None)

        graph = nx.Graph()

        for switch in switches:
            graph.add_node(switch.dp.id)

        switch_ports = {}

        for link in links:
            a = link.src.dpid
            b = link.dst.dpid

            weight = 100 if self.force_alternate and {a, b} == {1, 2} else 1
            graph.add_edge(a, b, weight=weight)
            switch_ports[(a, b)] = link.src.port_no
            switch_ports[(b, a)] = link.dst.port_no

        if not links and not self.graph.edges:
            self.logger.info('Waiting for topology links')
            return

        old_edges = set(map(frozenset, self.graph.edges()))
        new_edges = set(map(frozenset, graph.edges()))

        self.graph = graph
        self.switch_ports = switch_ports

        if old_edges != new_edges:
            self.logger.info(
                'Topology changed: %s',
                list(graph.edges())
            )

            self.clear_routes()
            self.install_known_routes()

            self.logger.info(
                'Forwarding and traffic-priority rules refreshed'
            )

    def clear_routes(self):
        for dp in self.datapaths.values():
            ofp = dp.ofproto
            parser = dp.ofproto_parser

            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofp.OFPFC_DELETE,
                table_id=0,
                out_port=ofp.OFPP_ANY,
                out_group=ofp.OFPG_ANY,
                match=parser.OFPMatch()
            )

            dp.send_msg(mod)
            self.install_table_miss(dp)

    def install_known_routes(self):
        planned = {}

        for src_mac, (src_switch, src_port) in self.hosts.items():
            for dst_mac, (dst_switch, dst_port) in self.hosts.items():

                if src_mac == dst_mac:
                    continue

                if (
                    src_switch not in self.graph
                    or dst_switch not in self.graph
                ):
                    continue

                if not nx.has_path(
                    self.graph, src_switch, dst_switch
                ):
                    continue

                path = nx.shortest_path(
                    self.graph, src_switch, dst_switch, weight="weight"
                )

                for i, switch_id in enumerate(path):

                    if switch_id == dst_switch:
                        out_port = dst_port
                    else:
                        out_port = self.switch_ports.get(
                            (switch_id, path[i + 1])
                        )

                    dp = self.datapaths.get(switch_id)

                    if dp is not None and out_port is not None:
                        planned[(switch_id, dst_mac)] = (
                            dp, out_port
                        )

        for (switch_id, dst_mac), (dp, out_port) in planned.items():

            parser = dp.ofproto_parser

            match = parser.OFPMatch(
                eth_dst=dst_mac
            )

            self.flow(
                dp,
                10,
                match,
                [parser.OFPActionOutput(out_port)]
            )

            self.install_traffic_flows(
                dp, dst_mac, out_port
            )

            self.logger.info(
                'Installed route to %s on switch %s via port %s',
                dst_mac, switch_id, out_port
            )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        dpid = dp.id
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        is_switch_port = any(
            switch_id == dpid and port == in_port
            for (switch_id, neighbor), port
            in self.switch_ports.items()
        )

        if not is_switch_port:
            location = (dpid, in_port)

            if self.hosts.get(eth.src) != location:
                self.hosts[eth.src] = location
                self.install_known_routes()

        out_port = ofp.OFPP_FLOOD

        if eth.dst in self.hosts:
            target, host_port = self.hosts[eth.dst]

            if target == dpid:
                out_port = host_port

            elif (
                dpid in self.graph
                and target in self.graph
                and nx.has_path(self.graph, dpid, target)
            ):
                path = nx.shortest_path(
                    self.graph, dpid, target, weight="weight"
                )

                out_port = self.switch_ports.get(
                    (path[0], path[1]),
                    ofp.OFPP_FLOOD
                )

                self.logger.info(
                    'Forwarding %s -> %s via %s',
                    eth.src, eth.dst, path
                )

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=eth.dst
            )

            self.flow(
                dp,
                10,
                match,
                [parser.OFPActionOutput(out_port)]
            )

        actions = []

        queue_id = self.classify_queue(pkt)

        if queue_id is not None:
            actions.append(
                parser.OFPActionSetQueue(queue_id)
            )

            self.logger.info(
                'Classified UDP traffic into queue %s',
                queue_id
            )

        actions.append(
            parser.OFPActionOutput(out_port)
        )

        packet_out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        dp.send_msg(packet_out)
