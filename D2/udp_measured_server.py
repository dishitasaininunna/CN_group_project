import socket
import time
import select

server_sockets = {}

for traffic, port in [("HIGH", 5003), ("NORMAL", 5004)]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    server_sockets[traffic] = sock

states = {}

for traffic in server_sockets:
    states[traffic] = {
        "received": set(),
        "original": set(),
        "retransmitted": set(),
        "missing": set(),
        "last_nack": {},
        "total": None,
        "initial_missing": None,
        "client": None,
        "recovery_deadline": None,
        "complete_time": None,
        "finished": False
    }

print("Dual-class measured UDP server listening", flush=True)
print("HIGH traffic: port 5003", flush=True)
print("NORMAL traffic: port 5004", flush=True)

while not all(states[t]["finished"] for t in states):
    readable, _, _ = select.select(list(server_sockets.values()), [], [], 0.2)

    for sock in readable:
        data, address = sock.recvfrom(1024)
        fields = data.decode().strip().split("|")

        if len(fields) < 2:
            continue

        traffic = fields[1]

        if traffic not in states:
            continue

        state = states[traffic]

        if state["finished"]:
            continue

        state["client"] = address

        if fields[0] == "DATA" and len(fields) >= 5:
            seq = int(fields[2])
            packet_type = fields[3]

            if state["total"] is not None and not 1 <= seq <= state["total"]:
                continue

            is_new = seq not in state["received"]

            if is_new:
                state["received"].add(seq)

                if packet_type == "ORIGINAL":
                    state["original"].add(seq)
                elif packet_type == "RETRANSMISSION":
                    state["retransmitted"].add(seq)

                state["missing"].discard(seq)

                if (
                    state["initial_missing"] is not None
                    and not state["missing"]
                    and state["complete_time"] is None
                ):
                    state["complete_time"] = time.monotonic()

            if is_new and packet_type == "RETRANSMISSION":
                ack_type = "RETRANSMISSION"
            elif is_new and packet_type == "ORIGINAL":
                ack_type = "ORIGINAL"
            else:
                ack_type = "DUPLICATE"

            sock.sendto(
                f"ACK|{traffic}|{seq}|{ack_type}".encode(), address
            )

        elif fields[0] == "END" and len(fields) == 3:
            announced_total = int(fields[2])

            if announced_total <= 0:
                continue

            if state["total"] is None:
                state["total"] = announced_total
                state["recovery_deadline"] = time.monotonic() + 20

                state["initial_missing"] = (
                    set(range(1, announced_total + 1)) - state["received"]
                )
                state["missing"] = set(state["initial_missing"])

                print(
                    f"{traffic}: Initially missing "
                    f"{sorted(state['initial_missing'])}",
                    flush=True
                )

                if not state["missing"]:
                    state["complete_time"] = time.monotonic()

            if announced_total != state["total"]:
                continue

    now = time.monotonic()

    for traffic, sock in server_sockets.items():
        state = states[traffic]

        if state["finished"] or state["total"] is None:
            continue

        state["missing"] = (
            set(range(1, state["total"] + 1)) - state["received"]
        )

        if state["missing"] and now < state["recovery_deadline"]:
            for seq in sorted(state["missing"]):
                if now - state["last_nack"].get(seq, 0) >= 0.5:
                    sock.sendto(
                        f"NACK|{traffic}|{seq}".encode(), state["client"]
                    )
                    state["last_nack"][seq] = now

                    print(
                        f"{traffic}: Missing packet {seq}; NACK sent",
                        flush=True
                    )

        final_missing = (
            set(range(1, state["total"] + 1)) - state["received"]
        )

        if not final_missing and state["complete_time"] is None:
            state["complete_time"] = now

        deadline_reached = now >= state["recovery_deadline"]
        ready_to_finish = (
            not final_missing
            and state["complete_time"] is not None
            and now - state["complete_time"] >= 2
        )

        if deadline_reached or ready_to_finish:
            total = state["total"]
            initial_count = len(state["initial_missing"])
            final_count = len(final_missing)

            summary = (
                f"ENDACK|{traffic}|{total}|{initial_count}|{final_count}"
            ).encode()

            for _ in range(5):
                sock.sendto(summary, state["client"])
                time.sleep(0.05)

            print(f"\n========== {traffic} SERVER SUMMARY ==========", flush=True)
            print(f"Total packets: {total}", flush=True)
            print(f"Initially missing: {initial_count}", flush=True)
            print(f"Recovered by retransmission: {len(state['retransmitted'])}", flush=True)
            print(f"Final missing: {final_count}", flush=True)
            print(f"Initial loss: {initial_count / total * 100:.2f}%", flush=True)
            print(f"Final loss: {final_count / total * 100:.2f}%", flush=True)

            state["finished"] = True

for sock in server_sockets.values():
    sock.close()

print("Both UDP traffic classes completed.", flush=True)
