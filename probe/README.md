# probe: Raspberry Pi Zero cable tester

A pocket device for the question "is this wall socket actually OK?". Plug it into any
Ethernet socket and an e-paper display shows, within seconds:

```
LINK  ✕ 100M full   flaps:0        negotiated speed and duplex, and whether the link bounced
PORT  ✓ Linksys-main / eth1         which switch or router port the cable reaches (LLDP)
IP    ✓ 192.168.1.222/24  7.5s     DHCP address and how long it took
GW    ✓ 192.168.1.1  1ms  0%       gateway reachability, latency, loss
DNS   ✓ 192.168.1.1
NET   ✓ <public address>           internet reachable
CABLE? (100M)                      verdict
```

It complements [`../monitor`](../monitor): the monitor tells you what the mesh thinks, the probe
tells you what the cable does.

> **Source not added yet.** This folder is reserved for the probe's code. Planned layout:
>
> ```
> probe/
>   README.md        you are here
>   hardware/        parts list, wiring, case
>   src/             the program that runs on the Pi
>   install.sh       setup on Raspberry Pi OS
> ```

Note for the verdict logic: a 100 Mbps reading can come from the **adapter** (a USB
Ethernet dongle may only do 100M) as well as from the cable, so the probe should say
which adapter it is using before blaming the cable.
