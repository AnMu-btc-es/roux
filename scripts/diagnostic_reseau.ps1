# Diagnostique la connexion reseau, en lecture seule : rien n'est modifie.

"--- interfaces ---"
Get-NetIPConfiguration -ErrorAction SilentlyContinue | ForEach-Object {
    "{0} : {1}" -f $_.InterfaceAlias, ($_.IPv4Address.IPAddress -join ", ")
}

"--- passerelle ---"
$route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue
if ($route) { "passerelle : " + (($route.NextHop | Select-Object -Unique) -join ", ") }

"--- DNS ---"
$dns = Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue
if ($dns) { "DNS : " + (($dns.ServerAddresses | Select-Object -Unique) -join ", ") }

"--- latence ---"
$ping = Test-Connection -ComputerName 1.1.1.1 -Count 3 -ErrorAction SilentlyContinue
if ($ping) {
    $moyenne = ($ping | Measure-Object ResponseTime -Average).Average
    "1.1.1.1 : {0} ms en moyenne" -f [math]::Round($moyenne, 0)
} else {
    "1.1.1.1 injoignable : la sortie reseau est peut-etre bloquee."
}
