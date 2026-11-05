# SDN-Based UDP Packet Loss Detection and Recovery

## Project Overview
This project implements a UDP client-server application integrated with
Software Defined Networking (SDN) using Mininet, Open vSwitch and OS-Ken.

The system demonstrates:
1. UDP packet-loss detection using sequence numbers.
2. ACK/NACK-based packet recovery through retransmissions.
3. Dynamic SDN rerouting after a link failure.
4. HIGH and NORMAL traffic classification using separate OVS queues.

## Network Topology
A diamond topology contains:
- Hosts: h1 and h2
- Switches: s1, s2, s3 and s4
- Primary path: h1-s1-s2-s4-h2
- Alternate path: h1-s1-s3-s4-h2

## Software Requirements
- Ubuntu
- Mininet
- Open vSwitch
- Python 3
- OS-Ken
- NetworkX
- Linux traffic control (tc)

## Files
- controller.py: Proposed SDN controller with traffic prioritization
- controller_baseline.py: Baseline controller without queue prioritization
- topology.py: Mininet diamond topology
- udp_measured_server.py: Dual-class UDP server
- udp_measured_client.py: Dual-class UDP client
- configure_qos.sh: Configures OVS queues
- test_results.txt: Measured experiment results and comparison
- baseline_high_client.log, baseline_normal_client.log: Baseline iperf3 logs
- proposed_high_client.log, proposed_normal_client.log: Proposed iperf3 logs
- flows_proposed_s1.txt: Proposed OpenFlow queue rules
- queue_stats_s1.txt: Queue statistics
- flows_s1.txt, flows_s2.txt, flows_s4.txt: OpenFlow rules

## Running the Project

### 1. Start the SDN Controller
Activate the virtual environment and run:

    cd ~/CN_project_D2
    source ~/sdn-venv/bin/activate
    osken-manager --ofp-tcp-listen-port 6653 --observe-links controller.py

### 2. Start Mininet
In another terminal:

    cd ~/CN_project_D2
    sudo mn --custom topology.py --topo diamond --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13

### 3. Configure QoS
In a third terminal:

    cd ~/CN_project_D2
    sudo ./configure_qos.sh

### 4. Run UDP Communication
At the Mininet prompt:

    h2 python3 /home/dishita_saini/CN_project_D2/udp_measured_server.py &

    h1 python3 /home/dishita_saini/CN_project_D2/udp_measured_client.py

### 5. Test Packet Loss and Recovery
Apply controlled packet loss:

    h1 tc qdisc add dev h1-eth0 root netem loss 10%

Run the UDP client again while the server is running:

    h1 python3 /home/dishita_saini/CN_project_D2/udp_measured_client.py

Remove the impairment afterwards:

    h1 tc qdisc del dev h1-eth0 root

### 6. Test Dynamic Rerouting
At the Mininet prompt:

    link s1 s3 down

Check the controller terminal for topology updates and alternate route installation.

Restore the link:

    link s1 s3 up

### 7. Inspect Queue Statistics and Flow Rules

    sh ovs-ofctl -O OpenFlow13 queue-stats s1
    sh ovs-ofctl -O OpenFlow13 dump-flows s1

## Experimental Results
Refer to test_results.txt for the measured packet-loss recovery,
traffic queue statistics and dynamic rerouting results.

## Cleanup
Exit Mininet:

    exit

Clean up Mininet:

    sudo mn -c

Stop the controller using Ctrl+C in its terminal.

## HIGH vs NORMAL Traffic Performance Evaluation

Traffic prioritization was evaluated using simultaneous UDP iperf3 traffic.

- HIGH traffic used UDP port 5003 and Queue 1.
- NORMAL traffic used UDP port 5004 and Queue 2.
- Each client offered 8 Mbps for 20 seconds.
- The shared QoS maximum rate was configured as 10 Mbps.

Receiver-side measurements:

| Metric | HIGH | NORMAL |
|---|---:|---:|
| Throughput | 7.04 Mbps | 3.80 Mbps |
| Jitter | 1.454 ms | 3.808 ms |
| Packet loss | 0% | 0% |

HIGH traffic achieved approximately 1.85 times the throughput of NORMAL traffic in this run. OVS queue statistics also confirmed separate traffic classification on s1-eth2.

These results demonstrate the observed effect of the configured traffic prioritization under simultaneous load. Results may vary between experimental runs. Detailed measurements are recorded in test_results.txt.


## Baseline vs Proposed Approach

A comparative performance experiment was conducted using two configurations.

### Baseline configuration
- Controller: controller_baseline.py
- No HIGH/NORMAL queue assignment or set_queue actions.
- All switch interfaces were shaped to 10 Mbps using Linux tc TBF.
- Both UDP traffic classes received default network treatment.

### Proposed configuration
- Controller: controller.py
- HIGH traffic (UDP port 5003) assigned to OVS Queue 1.
- NORMAL traffic (UDP port 5004) assigned to OVS Queue 2.
- HIGH queue minimum rate: 7 Mbps.
- NORMAL queue minimum rate: 1 Mbps.
- QoS maximum rate: 10 Mbps.

### Common test conditions
- Mininet diamond topology.
- Two simultaneous UDP iperf3 flows.
- Offered rate: 8 Mbps per flow.
- Test duration: 20 seconds.
- Receiver-side throughput, jitter and packet loss were measured.

### Comparative receiver-side results

| Metric | Baseline HIGH | Proposed HIGH | Baseline NORMAL | Proposed NORMAL |
|---|---:|---:|---:|---:|
| Throughput (Mbps) | 5.84 | 6.86 | 5.76 | 2.66 |
| Jitter (ms) | 2.015 | 1.573 | 0.366 | 4.973 |
| Packet loss (%) | 26 | 0 | 28 | 0 |

### Discussion

Without prioritization, the two traffic classes achieved nearly equal
throughput, but both experienced packet loss under simultaneous load.

With the proposed queue configuration, HIGH traffic received greater
throughput than NORMAL traffic. Both flows recorded zero packet loss
in this measured run. NORMAL traffic experienced higher jitter, showing
the trade-off introduced by prioritization.

The results demonstrate differentiated traffic treatment through
SDN-controlled OVS queues. They represent one run per configuration
and should not be interpreted as statistical averages or guaranteed
performance under all network conditions.

### Reproducing the baseline

1. Start the baseline controller:

       source ~/sdn-venv/bin/activate
       osken-manager --ofp-tcp-listen-port 6653 --observe-links controller_baseline.py

2. Start the Mininet diamond topology using the same remote controller
   connection described above.

3. At the Mininet prompt, apply 10 Mbps TBF shaping to all ten switch
   interfaces:

       sh for p in s1-eth1 s1-eth2 s1-eth3 s2-eth1 s2-eth2 s3-eth1 s3-eth2 s4-eth1 s4-eth2 s4-eth3; do tc qdisc replace dev $p root tbf rate 10mbit burst 32kb latency 50ms; done

4. Start iperf3 servers on h2 ports 5003 and 5004, then run simultaneous
   8 Mbps UDP clients from h1 for 20 seconds.

5. Read the receiver-side results from the iperf3 client output.

The baseline and proposed client logs are included in this directory.

## Experiment 8: End-to-End UDP Loss Detection and SDN Rerouting

### Objective
Verify that application-level UDP packet-loss detection triggers a
change in the SDN-controlled route, followed by successful packet recovery.

### Test Setup
- Mininet diamond topology with switches s1, s2, s3 and s4.
- UDP HIGH traffic on port 5003.
- UDP NORMAL traffic on port 5004.
- Artificial 30% packet loss applied to s1-eth2.
- The UDP client notifies the SDN controller when it detects missing packets.

### Results
| Metric | HIGH | NORMAL |
|---|---:|---:|
| Original packets | 30 | 30 |
| Initially missing | 12 | 11 |
| Initial packet loss | 40.00% | 36.67% |
| Retransmission attempts | 19 | 13 |
| Final missing packets | 0 | 0 |
| Final packet loss | 0.00% | 0.00% |
| Recovery duration | 620.25 ms | 619.84 ms |

All 23 initially missing packets were recovered.

### SDN Rerouting Verification
After receiving the UDP loss notification, the controller changed
the preferred route away from s1-s2 and installed the alternate path:

h1 -> s1 -> s3 -> s4 -> h2

The controller logs confirmed the loss notification and alternate
route installation. The OpenFlow flow table on s1 confirmed that
traffic destined for h2 was forwarded through s1-eth3 (towards s3).

### Final Connectivity Check
After removing the artificial packet-loss condition, Mininet pingall
reported 0% packet loss (2/2 packets received).

### Evidence Files
- integrated_client.log
- integrated_server.log
- integrated_flows_s1.txt
- integrated_flows_s3.txt
- integrated_flows_s4.txt

This experiment demonstrates end-to-end interaction between the UDP
application and the SDN controller: packet-loss detection triggers
dynamic route selection, while retransmission recovers missing data.

### Reproducing the End-to-End Integration Test

Use two Ubuntu terminals.

Terminal 1 — start the controller:

```bash
source ~/sdn-venv/bin/activate
cd ~/CN_project_D2_integrated
rm -f /tmp/sdn_controller_control.sock
osken-manager --ofp-tcp-listen-port 6653 --observe-links controller.py

cd ~/CN_project_D2_integrated
sudo mn --custom topology.py --topo diamond --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13
cd ~/CN_project_D2_integrated

cat >> README.md <<'EOF'

### Reproducing the End-to-End Integration Test

Open two Ubuntu terminals.

Terminal 1: Activate the environment and start the controller:
1. source ~/sdn-venv/bin/activate
2. cd ~/CN_project_D2_integrated
3. rm -f /tmp/sdn_controller_control.sock
4. osken-manager --ofp-tcp-listen-port 6653 --observe-links controller.py

Terminal 2: Start Mininet:
1. cd ~/CN_project_D2_integrated
2. sudo mn --custom topology.py --topo diamond --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13

In the Mininet CLI:
1. pingall
2. sh tc qdisc replace dev s1-eth2 root netem loss 30%
3. h2 python3 udp_measured_server.py > /tmp/integrated_server.log 2>&1 &
4. sh sleep 2
5. h1 python3 udp_measured_client.py > /tmp/integrated_client.log 2>&1 &
6. sh sleep 25
7. h1 cat /tmp/integrated_client.log
8. h2 cat /tmp/integrated_server.log
9. sh ovs-ofctl -O OpenFlow13 dump-flows s1

Verify the controller reports the UDP loss notification and installs
the alternate route through s1-s3-s4.

After testing:
1. sh tc qdisc del dev s1-eth2 root
2. pingall
3. exit

Stop the controller with Ctrl+C.
