//
// Created by Jayde on 10/16/2026.
//

#include <iostream>
#include <string>
#include "crow.h"

using namespace std;

int main() {

    /*
     * MouseAction(Click) gives a name
     * GetData(Name);
     * Sends a request to the database to pull data from a specific area
     * returns to user
     * needs to be quick
     */
    crow::SimpleApp host;

    CROW_ROUTE(host, "/")([](){
        return "Hi Jonas, Luis, and Jovon!";
    });

    host.port(18080).multithreaded().run();
}
//
// Created by Jayde on 10/16/2026.
//