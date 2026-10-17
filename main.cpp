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
     *
    * URL req				Schema
GET data	Takes a location and then returns all the data stored in the SQL schema for that location			Location, Hazard Rating, Name of the Location
POST danger	User will be able to make a report this report contains  a title, a descripttion and image. The post request should be able to accept these pieces of data and use an alg to determine the closest location to tie this report to
     */
    crow::SimpleApp host;

    CROW_ROUTE(host, "/")([](){
        return "Hi Jonas, Luis, and Jovon!";
    });

    host.port(18080).multithreaded().run();

    /*
     * Handling click events in Corw
    *  CROW_ROUTE(app, "/api/click-data")
    ([](){
        // Create a JSON write value object
        crow::json::wvalue response_data;
        response_data["status"] = "success";
        response_data["message"] = "Mouse clicked successfully!";
        response_data["timestamp"] = 1697500000; // Example timestamp

        // Returning wvalue sets the Content-Type to application/json automatically
        return response_data;
    });
     */
}
//
// Created by Jayde on 10/16/2026.
//