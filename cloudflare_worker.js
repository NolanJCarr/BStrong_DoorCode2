export default {
  // 1. ADD 'env' HERE
  async fetch(request, env) {
    try {
      const targetUrlHeader = request.headers.get("X-Target-Url") || request.headers.get("x-target-url");
      const targetUrl = targetUrlHeader || "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token";
      
      const authPayload = {
        clientId: env.VAGARO_CLIENT_ID,
        clientSecretKey: env.VAGARO_CLIENT_SECRET
      };

      let finalBody = null;
      if (targetUrl.includes("generate-access-token")) {
        finalBody = JSON.stringify(authPayload);
      } else {
        if (request.method === "POST" || request.method === "PUT") {
             finalBody = await request.text();
        }
      }

      const cleanHeaders = new Headers();
      cleanHeaders.set("Content-Type", "application/json");
      cleanHeaders.set("Accept", "application/json");
      cleanHeaders.set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36");

      const vagaroToken = request.headers.get("accessToken") || request.headers.get("accesstoken");
      if (vagaroToken) {
         cleanHeaders.set("accessToken", vagaroToken);
      }

      const newRequest = new Request(targetUrl, {
        method: request.method === "OPTIONS" ? "OPTIONS" : "POST", 
        headers: cleanHeaders,
        body: finalBody || "{}"
      });

      return await fetch(newRequest);
      
    } catch (e) {
      return new Response(JSON.stringify({ error: "Worker Proxy Error", details: e.message }), { 
        status: 500,
        headers: { "Content-Type": "application/json" }
      });
    }
  }
};