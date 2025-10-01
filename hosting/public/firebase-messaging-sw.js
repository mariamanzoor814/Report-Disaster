// firebase-messaging-sw.js
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js');

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

messaging.onBackgroundMessage(function(payload) {
  const title = payload.notification?.title || 'New report';
  const options = {
    body: payload.notification?.body || '',
    icon: payload.notification?.icon || '/favicon.ico'
  };
  self.registration.showNotification(title, options);
});
