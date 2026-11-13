SynCanvas Collaborative Whiteboard
GitHub repository: https://github.com/poddarchhatri-source/Collaborative-Whiteboard

RUN ON WINDOWS
1. In GitHub, open Actions and download the latest:
   - syncanvas-windows-server
   - syncanvas-windows-client
2. Extract the server download into 01_Server_Application and the client
   download into 02_Client_Application.
3. Run 01_Server_Application/server.exe and click Start Server.
4. Run 02_Client_Application/client.exe. Connect to 127.0.0.1 on the same
   computer, or enter the server's LAN address from another computer.

RUN ON LINUX
1. In GitHub, open Actions and download the latest:
   - syncanvas-linux-server
   - syncanvas-linux-client
2. Extract the server download into 01_Server_Application and the client
   download into 02_Client_Application.
3. From the project folder, run:
   chmod +x 01_Server_Application/server 02_Client_Application/client
   ./01_Server_Application/server
4. Click Start Server, then in another terminal run:
   ./02_Client_Application/client
5. Connect to 127.0.0.1 on the same computer, or use the server's LAN address.

The GitHub workflow builds the Windows and Linux executables on their native
operating systems. Run it from Actions, or push a change to the main branch.
No Windows executable is included until that workflow has run.

PROJECT FOLDERS
01_Server_Application/          Server executable and source copy
02_Client_Application/          Client executable and source copy
03_Source_Code/                 Main source code and build instructions
04_Technical_Documentation/     Technical report PDF
05_Presentation/                Project PDF; no PowerPoint file was found
06_Explanation_Video/            Supplied project video
