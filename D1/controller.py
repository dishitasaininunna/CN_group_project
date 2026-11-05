import networkx as nx

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
from os_ken.lib.packet import packet, ethernet, ether_types


class Part3Controller(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.graph = nx.Graph()
        self.datapaths = {}
        self.hosts = {}
        self.switch_ports = {}

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

            graph.add_edge(a, b)
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
            self.logger.info('Topology changed: %s', list(graph.edges()))
            self.clear_routes()
            self.install_known_routes()
            self.logger.info('Forwarding rules refreshed for new topology')

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

                if src_switch not in self.graph or dst_switch not in self.graph:
                    continue

                if not nx.has_path(self.graph, src_switch, dst_switch):
                    continue

                path = nx.shortest_path(self.graph, src_switch, dst_switch)

                for i, switch_id in enumerate(path):
                    if switch_id == dst_switch:
                        out_port = dst_port
                    else:
                        out_port = self.switch_ports.get(
                            (switch_id, path[i + 1])
                        )

                    dp = self.datapaths.get(switch_id)

                    if dp is not None and out_port is not None:
                        planned[(switch_id, dst_mac)] = (dp, out_port)

        for (switch_id, dst_mac), (dp, out_port) in planned.items():
            match = dp.ofproto_parser.OFPMatch(eth_dst=dst_mac)
            self.flow(
                dp,
                10,
                match,
                [dp.ofproto_parser.OFPActionOutput(out_port)]
            )

            self.logger.info(
                'Installed route to %s on switch %s via port %s',
                dst_mac,
                switch_id,
                out_port
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
            for (switch_id, neighbor), port in self.switch_ports.items()
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
                path = nx.shortest_path(self.graph, dpid, target)
                out_port = self.switch_ports.get(
                    (path[0], path[1]),
                    ofp.OFPP_FLOOD
                )

                self.logger.info(
                    'Forwarding %s -> %s via %s',
                    eth.src,
                    eth.dst,
                    path
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

        packet_out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=[parser.OFPActionOutput(out_port)],
            data=msg.data
        )

        dp.send_msg(packet_out)
