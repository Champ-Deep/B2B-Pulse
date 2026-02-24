import express from "express";
import cors from "cors";
import axios from "axios";
import qrcode from "qrcode-terminal";
import QRCode from "qrcode";

// whatsapp-web.js uses require-style imports
const { Client, LocalAuth } = require("whatsapp-web.js");

const app = express();
app.use(express.json());
app.use(cors());

const PORT = process.env.PORT || 3001;
const BACKEND_WEBHOOK_URL =
  process.env.BACKEND_WEBHOOK_URL ||
  "http://backend:8000/api/webhooks/whatsapp-link";

// State
let currentQR: string | null = null;
let isReady = false;
let monitoredGroups: Set<string> = new Set();

// URL regex for extracting links from messages
const URL_REGEX = /https?:\/\/[^\s<>"{}|\\^`\[\]]+/gi;

// Social media domains we care about
const SOCIAL_DOMAINS = [
  "linkedin.com",
  "www.linkedin.com",
  "instagram.com",
  "www.instagram.com",
  "facebook.com",
  "www.facebook.com",
  "fb.com",
];

// Initialize WhatsApp client
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: ".wwebjs_auth" }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--disable-gpu",
    ],
  },
});

// QR Code event
client.on("qr", (qr: string) => {
  currentQR = qr;
  console.log("QR Code received. Scan with WhatsApp:");
  qrcode.generate(qr, { small: true });
});

// Ready event
client.on("ready", () => {
  isReady = true;
  currentQR = null;
  console.log("WhatsApp client is ready!");
});

// Authentication event
client.on("authenticated", () => {
  console.log("WhatsApp client authenticated");
});

// Disconnected event
client.on("disconnected", (reason: string) => {
  isReady = false;
  console.log("WhatsApp client disconnected:", reason);
});

// Message event — the core listener
client.on("message", async (message: any) => {
  try {
    // Only process messages from monitored groups
    const chat = await message.getChat();
    if (!chat.isGroup) return;

    // Check if this group is monitored (by name or ID)
    const groupId = chat.id._serialized;
    const groupName = chat.name;
    if (!monitoredGroups.has(groupId) && !monitoredGroups.has(groupName)) return;

    // Extract URLs from message
    const urls = message.body?.match(URL_REGEX) || [];
    if (urls.length === 0) return;

    // Filter for social media URLs
    for (const url of urls) {
      try {
        const parsedUrl = new URL(url);
        const isSocial = SOCIAL_DOMAINS.some((domain) =>
          parsedUrl.hostname.includes(domain)
        );

        if (isSocial) {
          console.log(`Social media link detected in ${groupName}: ${url}`);

          // Forward to backend
          const contact = await message.getContact();
          await axios.post(BACKEND_WEBHOOK_URL, {
            url: url,
            group_name: groupName,
            sender: contact.pushname || contact.number || "Unknown",
            timestamp: new Date(message.timestamp * 1000).toISOString(),
          });

          console.log(`Forwarded to backend: ${url}`);
        }
      } catch (err) {
        console.error(`Error processing URL ${url}:`, err);
      }
    }
  } catch (err) {
    console.error("Error processing message:", err);
  }
});

// === Express API endpoints ===

// Health check
app.get("/health", (_req, res) => {
  res.json({
    status: isReady ? "connected" : "disconnected",
    qr_available: currentQR !== null,
    monitored_groups: Array.from(monitoredGroups),
  });
});

// Get QR code for scanning (returns data URL for embedding)
app.get("/qr", async (_req, res) => {
  if (isReady) {
    res.json({ status: "already_connected", ready: true, qr: null });
  } else if (currentQR) {
    try {
      const qrDataUrl = await QRCode.toDataURL(currentQR, { width: 256 });
      res.json({ status: "awaiting_scan", ready: false, qr: qrDataUrl });
    } catch {
      res.json({ status: "awaiting_scan", ready: false, qr: currentQR });
    }
  } else {
    res.json({ status: "initializing", ready: false, qr: null });
  }
});

// Configure monitored groups
app.post("/config", (req, res) => {
  const { groups } = req.body;
  if (!Array.isArray(groups)) {
    res.status(400).json({ error: "groups must be an array of group names or IDs" });
    return;
  }
  monitoredGroups = new Set(groups);
  console.log(`Updated monitored groups: ${Array.from(monitoredGroups).join(", ")}`);
  res.json({
    status: "updated",
    monitored_groups: Array.from(monitoredGroups),
  });
});

// List available groups
app.get("/groups", async (_req, res) => {
  if (!isReady) {
    res.status(503).json({ error: "WhatsApp client not ready" });
    return;
  }
  try {
    const chats = await client.getChats();
    const groups = chats
      .filter((chat: any) => chat.isGroup)
      .map((chat: any) => ({
        id: chat.id._serialized,
        name: chat.name,
        participant_count: chat.participants?.length || 0,
      }));
    res.json({ groups });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch groups" });
  }
});

// Start Express server
app.listen(PORT, () => {
  console.log(`WhatsApp sidecar API running on port ${PORT}`);
});

// Initialize WhatsApp client
client.initialize();

// Graceful shutdown
process.on("SIGINT", async () => {
  console.log("Shutting down...");
  await client.destroy();
  process.exit(0);
});
