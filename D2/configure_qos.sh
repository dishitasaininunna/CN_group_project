#!/bin/bash

for switch in s1 s2 s3 s4
do
    for port in $(sudo ovs-vsctl list-ports "$switch" | grep "^${switch}-eth")
    do
        sudo ovs-vsctl \
        -- --id=@q1 create Queue other-config:min-rate=7000000 other-config:max-rate=10000000 \
        -- --id=@q2 create Queue other-config:min-rate=1000000 other-config:max-rate=10000000 \
        -- --id=@qos create QoS type=linux-htb other-config:max-rate=10000000 queues:1=@q1 queues:2=@q2 \
        -- set Port "$port" qos=@qos

        echo "Configured QoS on $port"
    done
done
