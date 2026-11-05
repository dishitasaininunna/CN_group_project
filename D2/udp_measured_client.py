import socket
import time

client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
client_socket.settimeout(0.05)

server_addresses = {
    "HIGH": ("10.0.0.2", 5003),
    "NORMAL": ("10.0.0.2", 5004)
}

total_packets = 30
send_interval = 0.4
deadline = time.monotonic() + 60

states = {}

for traffic in server_addresses:
    states[traffic] = {
        "next_sequence": 1,
        "next_send_time": time.monotonic(),
        "next_end_time": time.monotonic(),
        "acked": set(),
        "retransmitted": set(),
        "attempts": 0,
        "last_retransmission": {},
        "nack_times": {},
        "recovery_ack_times": {},
        "first_nack": None,
        "initial_missing": None,
        "final_missing": None,
        "endack": False
    }

start_time = time.monotonic()
controller_notified = False
controller_socket_path = "/tmp/sdn_controller_control.sock"

print("Starting dual-class measured SDN-UDP test", flush=True)
print("HIGH traffic: UDP port 5003", flush=True)
print("NORMAL traffic: UDP port 5004", flush=True)

while time.monotonic() < deadline:
    now = time.monotonic()

    for traffic, address in server_addresses.items():
        state = states[traffic]

        if state["next_sequence"] <= total_packets and now >= state["next_send_time"]:
            seq = state["next_sequence"]
            message = f"DATA|{traffic}|{seq}|ORIGINAL|Measured packet"
            client_socket.sendto(message.encode(), address)

            print(f"{traffic}: Sent original packet {seq}", flush=True)

            state["next_sequence"] += 1
            state["next_send_time"] += send_interval

        if state["next_sequence"] > total_packets and not state["endack"] and now >= state["next_end_time"]:
            client_socket.sendto(
                f"END|{traffic}|{total_packets}".encode(), address
            )
            state["next_end_time"] = now + 1

    if all(states[t]["endack"] for t in states):
        break

    try:
        data, address = client_socket.recvfrom(1024)
    except socket.timeout:
        continue

    fields = data.decode().strip().split("|")

    if len(fields) < 2:
        continue

    if fields[0] == "ACK" and len(fields) >= 4:
        traffic = fields[1]

        if traffic not in states:
            continue

        state = states[traffic]
        seq = int(fields[2])
        ack_type = fields[3]

        state["acked"].add(seq)

        print(f"{traffic}: ACK received for packet {seq} ({ack_type})", flush=True)

        if ack_type == "RETRANSMISSION" and seq in state["nack_times"]:
            state["recovery_ack_times"][seq] = time.monotonic()

    elif fields[0] == "NACK" and len(fields) == 3:
        traffic = fields[1]

        if traffic not in states:
            continue

        state = states[traffic]
        seq = int(fields[2])
        now = time.monotonic()

        if not 1 <= seq <= total_packets:
            continue

        if not controller_notified:
            try:
                control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                control_socket.connect(controller_socket_path)
                control_socket.sendall(f"LOSS|{traffic}|{seq}".encode())
                control_socket.close()
                controller_notified = True
                print("SDN controller notified: UDP packet loss detected", flush=True)
            except OSError as error:
                print(f"Could not notify SDN controller: {error}", flush=True)

        if seq not in state["nack_times"]:
            state["nack_times"][seq] = now

            if state["first_nack"] is None:
                state["first_nack"] = now

        if now - state["last_retransmission"].get(seq, 0) >= 0.25:
            message = f"DATA|{traffic}|{seq}|RETRANSMISSION|Recovered packet"
            client_socket.sendto(message.encode(), server_addresses[traffic])

            state["retransmitted"].add(seq)
            state["attempts"] += 1
            state["last_retransmission"][seq] = now

            print(
                f"{traffic}: Retransmission attempt {state['attempts']} "
                f"for packet {seq}",
                flush=True
            )

    elif fields[0] == "ENDACK" and len(fields) == 5:
        traffic = fields[1]

        if traffic not in states:
            continue

        state = states[traffic]
        state["endack"] = True
        state["initial_missing"] = int(fields[3])
        state["final_missing"] = int(fields[4])

        print(f"{traffic}: Final server summary received", flush=True)

finish_time = time.monotonic()

for traffic, state in states.items():
    print(f"\n========== {traffic} TRAFFIC SUMMARY ==========", flush=True)
    print(f"Original packets: {total_packets}", flush=True)
    print(f"Packets acknowledged: {len(state['acked'])}/{total_packets}", flush=True)
    print(f"Retransmitted sequences: {sorted(state['retransmitted'])}", flush=True)
    print(f"Retransmission attempts: {state['attempts']}", flush=True)

    if state["initial_missing"] is not None:
        print(f"Initially missing: {state['initial_missing']}/{total_packets}", flush=True)
        print(
            f"Initial loss: {state['initial_missing'] / total_packets * 100:.2f}%",
            flush=True
        )
        print(f"Final missing: {state['final_missing']}", flush=True)
        print(
            f"Final loss: {state['final_missing'] / total_packets * 100:.2f}%",
            flush=True
        )
    else:
        print("Server summary not received.", flush=True)

    if state["recovery_ack_times"] and state["first_nack"] is not None:
        recovery = max(state["recovery_ack_times"].values()) - state["first_nack"]
        print(f"Recovery duration: {recovery * 1000:.2f} ms", flush=True)
    else:
        print("Recovery duration: Not measured", flush=True)

    if state["endack"] and state["final_missing"] == 0:
        print("RESULT: All packets recovered.", flush=True)
    elif state["endack"]:
        print("RESULT: Some packets remain missing.", flush=True)
    else:
        print("RESULT: Test timed out.", flush=True)

print(f"\nTotal test duration: {finish_time - start_time:.2f} seconds", flush=True)

client_socket.close()
