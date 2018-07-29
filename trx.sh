#!/bin/bash
rm payments.sh -rf || true
python3 trxpool.py  -c config_snapshot.json -y

