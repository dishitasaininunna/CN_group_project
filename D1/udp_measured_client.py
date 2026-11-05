import socket
import time

client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
client_socket.settimeout(0.05)

server_address = ("10.0.0.2", 5003)
total_packets = 30
send_interval = 0.4

acked_packets = set()
retransmitted_sequences = set()
retransmission_attempts = 0
last_retransmission_time = {}
first_nack_time = None
nack_times = {}
recovery_ack_times = {}
server_initial_missing = None
server_final_missing = None
endack_received = False

start_time = time.monotonic()
deadline = start_time + 45
next_sequence = 1
next_send_time = start_time
next_end_time = start_time
first_send_times = {}

print("Starting measured SDN-UDP test", flush=True)
print(f"Total numbered packets: {total_packets}", flush=True)

while time.monotonic() < deadline:
    now = time.monotonic()

    if next_sequence <= total_packets and now >= next_send_time:
        sequence_number = next_sequence
        message = f"DATA|{sequence_number}|ORIGINAL|Hello from measured client"

        client_socket.sendto(message.encode(), server_address)
        first_send_times[sequence_number] = time.monotonic()
        print(f"Sent original packet {sequence_number}", flush=True)

        next_sequence += 1
        next_send_time += send_interval

    if next_sequence > total_packets and now >= next_end_time:
        client_socket.sendto(
            f"END|{total_packets}".encode(), server_address
        )
        next_end_time = now + 1

    try:
        data, address = client_socket.recvfrom(1024)
    except socket.timeout:
        continue

    fields = data.decode().strip().split("|")

    if fields[0] == "ACK" and len(fields) >= 2:
        sequence_number = int(fields[1])
        ack_type = fields[2] if len(fields) >= 3 else "UNKNOWN"
        acked_packets.add(sequence_number)

        print(
            f"Received ACK for packet {sequence_number} ({ack_type})",
            flush=True
        )

        if (
            ack_type == "RETRANSMISSION"
            and sequence_number in nack_times
            and sequence_number not in recovery_ack_times
        ):
            recovery_ack_times[sequence_number] = time.monotonic()

    elif fields[0] == "NACK" and len(fields) == 2:
        sequence_number = int(fields[1])
        now = time.monotonic()

        if sequence_number < 1 or sequence_number > total_packets:
            continue

        if sequence_number not in nack_times:
            nack_times[sequence_number] = now
            if first_nack_time is None:
                first_nack_time = now
                print("First missing-packet request received", flush=True)

        if now - last_retransmission_time.get(sequence_number, 0) >= 0.25:
            message = (
                f"DATA|{sequence_number}|RETRANSMISSION|Recovered packet"
            )
            client_socket.sendto(message.encode(), server_address)
            retransmitted_sequences.add(sequence_number)
            retransmission_attempts += 1
            last_retransmission_time[sequence_number] = now

            print(
                f"Retransmission attempt {retransmission_attempts}: "
                f"packet {sequence_number}",
                flush=True
            )

    elif fields[0] == "ENDACK" and len(fields) == 4:
        endack_received = True
        server_initial_missing = int(fields[2])
        server_final_missing = int(fields[3])
        print("Received final server summary", flush=True)

        if server_final_missing == 0:
            break

finish_time = time.monotonic()
elapsed = finish_time - start_time

print("\n========== CLIENT SUMMARY ==========", flush=True)
print(f"Original packets sent: {total_packets}", flush=True)
print(f"Packets acknowledged: {len(acked_packets)}/{total_packets}", flush=True)
print(
    f"Packets without ACK: {total_packets - len(acked_packets)}",
    flush=True
)
print(
    f"Sequences retransmitted: {sorted(retransmitted_sequences)}",
    flush=True
)
print(f"Retransmission attempts: {retransmission_attempts}", flush=True)

if server_initial_missing is not None:
    print(
        f"Initially missing at first END: "
        f"{server_initial_missing}/{total_packets}",
        flush=True
    )
    print(
        f"Initial loss percentage: "
        f"{server_initial_missing / total_packets * 100:.2f}%",
        flush=True
    )
    print(f"Final missing packets: {server_final_missing}", flush=True)
    print(
        f"Final loss percentage: "
        f"{server_final_missing / total_packets * 100:.2f}%",
        flush=True
    )
else:
    print("Server final loss statistics were not received.", flush=True)

if recovery_ack_times and first_nack_time is not None:
    last_recovery_time = max(recovery_ack_times.values()) - first_nack_time
    print(
        f"Recovery duration (first NACK to last recovered-packet ACK): "
        f"{last_recovery_time * 1000:.2f} ms",
        flush=True
    )
elif first_nack_time is None:
    print("No NACK received; recovery time is not applicable.", flush=True)
else:
    print("No confirmed retransmission recovery time was measured.", flush=True)

print(f"Total test duration: {elapsed:.2f} seconds", flush=True)

if endack_received and server_final_missing == 0:
    print("RESULT: Server received all numbered packets.", flush=True)
elif endack_received:
    print("RESULT: Some packets remained unrecovered.", flush=True)
else:
    print("RESULT: Test timed out before receiving the final summary.", flush=True)

client_socket.close()
