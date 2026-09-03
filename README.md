Traditionally, FreshPorts determines a port version via `make -V PORTVERSION`

For each commit against a given port, that command is run and the current value
is obtained.

However, for some ports, the commit which affects the `PORTVERSION` value does not
touch the port.  FreshPorts does not run that command for the port[s] because it
does not know the details.

This code hopes to parse changes to Mk/bsd.default-versions.mk, detect any changes
which might affect PORTVERSION, and then update the affected ports.

See https://github.com/FreshPorts/freshports/issues/659 for more background.
