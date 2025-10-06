// firebase-messaging-sw.js
importScripts("https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js");

const firebaseConfig = {
  apiKey: "AIzaSyCcfgV1aIVFAFLsXrKarFTT3hTarsIy8ao",
  authDomain: "report-disasters.firebaseapp.com",
  projectId: "report-disasters",
  storageBucket: "report-disasters.firebasestorage.app",
  messagingSenderId: "194910232971",
  appId: "1:194910232971:web:bf7aacf7ed7dbf73f68f8e",
  measurementId: "G-N19ZTE6961"
};

firebase.initializeApp(firebaseConfig);

const messaging = firebase.messaging();

// Background notification handler
messaging.onBackgroundMessage((payload) => {
  console.log("[firebase-messaging-sw.js] Background message ", payload);

  const notification = payload.notification || {};
  const title = notification.title || "Report Disasters";
  const options = {
    body: notification.body || "",
    icon: notification.icon || "/icon.png",
    data: payload.data || {},
  };

  self.registration.showNotification(title, options);
});

// Notification click
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      if (clientList.length > 0) {
        return clientList[0].focus();
      }
      return clients.openWindow("/");
    })
  );
});
