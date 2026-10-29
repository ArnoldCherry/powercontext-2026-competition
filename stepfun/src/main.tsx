import ReactDOM from "react-dom/client";
import App from "./App.tsx";
import BenchPage from "./bench/BenchPage.tsx";
import "./styles.css";

const isBench = window.location.pathname.startsWith("/bench");
ReactDOM.createRoot(document.getElementById("root")!).render(isBench ? <BenchPage /> : <App />);
