#!/bin/sh
set -eu

mkdir -p /run/kea /var/lib/kea /var/log/kea
LEASES=/var/lib/kea/kea-leases6.csv

# Seed a few leases so GET /api/v1/leases has something to show.
# fd00::101 is written twice (a renewal), as Kea's append-only memfile does.
if [ ! -f "$LEASES" ]; then
    now=$(date +%s)
    {
        echo "address,duid,valid_lifetime,expire,subnet_id,pref_lifetime,lease_type,iaid,prefix_len,fqdn_fwd,fqdn_rev,hostname,hwaddr,state,user_context,hwtype,hwaddr_source"
        echo "fd00::100,00:03:00:01:52:54:00:aa:bb:01,4000,$((now + 3000)),1,3000,0,1,128,0,0,lab-client,52:54:00:aa:bb:01,0,,1,4"
        echo "fd00::101,00:03:00:01:52:54:00:aa:bb:02,4000,$((now + 1000)),1,3000,0,1,128,0,0,,52:54:00:aa:bb:02,0,,1,4"
        echo "fd00::101,00:03:00:01:52:54:00:aa:bb:02,4000,$((now + 4000)),1,3000,0,1,128,0,0,,52:54:00:aa:bb:02,0,,1,4"
    } > "$LEASES"
fi

kea-dhcp6 -c /etc/kea/kea-dhcp6.conf &

# Wait for Kea's control socket before starting the API
i=0
while [ ! -S /run/kea/kea6-ctrl-socket ] && [ $i -lt 50 ]; do
    sleep 0.2
    i=$((i + 1))
done

exec /opt/dhcpv6-api/venv/bin/uvicorn --factory dhcpv6_api.main:create_app --host 0.0.0.0 --port 8000
