@echo off
netsh interface ipv6 set dnsservers "Ethernet" static 2001:ee0:b004:306b::1 primary
netsh interface ipv6 add dnsservers "Ethernet" 2001:4860:4860::8888 index=2
exit /b 0
