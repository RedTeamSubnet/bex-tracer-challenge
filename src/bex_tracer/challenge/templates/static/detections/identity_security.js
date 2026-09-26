async function detectMicrosoftSso() {
  return await new Promise(resolve => {
    const channel = new MessageChannel();
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      channel.port1.close();
      resolve(value);
    };
    channel.port1.onmessage = event => finish(event.data?.body?.method === "HandshakeResponse");
    window.postMessage({
      channel: "53ee284d-920a-4b59-9d30-a60315b26836",
      responseId: "exc-probe",
      body: {method: "Handshake"}
    }, window.origin, [channel.port2]);
    setTimeout(() => finish(false), 250);
  });
}
window.detect_identity_security = async function () {
  return {
    "Microsoft Single Sign On": await detectMicrosoftSso(),
    "Trust Wallet": !!document.getElementById("in-page-channel-node-id")
  };
};
